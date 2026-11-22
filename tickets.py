"""Tickets: customizable panels with several buttons (each with its own emoji, color, category,
support role and welcome message), claim + close buttons, optional log channel with transcripts.

Buttons are handled through the `on_interaction` event (custom ids start with `tk:`), so panels keep
working after a restart without any extra registration.
"""
import asyncio
import io
import logging

import discord
from discord.ext import commands

from utils.helpers import BLURPLE, embed, err, ok, parse_color
from utils.levelmath import fill
from utils.store import store
from utils.ticketcore import (DEFAULT_WELCOME, MAX_BUTTONS, MAX_PANELS, STYLE_NAMES, clean_channel_part, fill_defaults,
                              new_button, new_panel, next_id, normalize_emoji, safe_channel_name, ticket_owner)

log = logging.getLogger("tickets")

STYLE_MAP = {"blurple": discord.ButtonStyle.primary, "green": discord.ButtonStyle.success,
             "red": discord.ButtonStyle.danger, "grey": discord.ButtonStyle.secondary}
PLACEHOLDERS = "{user} {username} {server} {type} {number}"


# ------------------------------------------------------------------ data helpers
def tcfg(gid) -> dict:
    return fill_defaults(store.get(gid).setdefault("tickets", {}))


def staff_role_ids(guild: discord.Guild) -> set:
    c = tcfg(guild.id)
    ids = {c["support_role"]}
    for panel in c["panels"].values():
        ids.update(b["support_role"] for b in panel["buttons"])
    ids.discard(None)
    return ids


def is_staff(member: discord.Member) -> bool:
    return member.guild_permissions.manage_channels or any(r.id in staff_role_ids(member.guild) for r in member.roles)


def support_roles(guild: discord.Guild, btn: dict = None) -> list:
    ids = {tcfg(guild.id)["support_role"], (btn or {}).get("support_role")}
    return [r for i in ids if i and (r := guild.get_role(i))]


def server_emojis(guild: discord.Guild):
    return [(e.name, str(e)) for e in guild.emojis]


def panel_embed(panel: dict) -> discord.Embed:
    emb = discord.Embed(title=panel["title"] or None, description=panel["description"] or None, color=panel["color"])
    if not (panel["title"] or panel["description"] or panel["image"]):
        emb.description = "Press a button below to open a ticket."
    if panel["image"]:
        emb.set_image(url=panel["image"])
    if panel["thumbnail"]:
        emb.set_thumbnail(url=panel["thumbnail"])
    if panel["footer"]:
        emb.set_footer(text=panel["footer"])
    return emb


def panel_view(pid: str, panel: dict) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for i, b in enumerate(panel["buttons"]):
        view.add_item(discord.ui.Button(label=b["label"] or None, emoji=b["emoji"] or None, style=STYLE_MAP[b["style"]],
                                        custom_id=f"tk:open:{pid}:{b['id']}", row=i // 5))
    return view


def controls_view(claimed: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="tk:close"))
    view.add_item(discord.ui.Button(label="Claimed" if claimed else "Claim", emoji="🙋", style=discord.ButtonStyle.secondary,
                                    custom_id="tk:claim", disabled=claimed))
    return view


class Tickets(commands.Cog, name="Tickets"):
    """Custom ticket panels with multiple buttons, custom emoji and welcome messages."""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ panel publishing (also used by the website)
    async def publish_panel(self, guild: discord.Guild, pid: str, channel: discord.TextChannel = None) -> discord.Message:
        """Post the panel, or edit the existing panel message in place."""
        panel = tcfg(guild.id)["panels"][pid]
        target = channel or guild.get_channel(panel["channel"])
        if not isinstance(target, discord.TextChannel):
            raise ValueError("Pick a channel for this panel.")
        emb, view = panel_embed(panel), panel_view(pid, panel)
        old_id, old_channel = panel.get("message_id"), panel["channel"]
        if old_id and target.id == old_channel:
            try:
                msg = await target.fetch_message(old_id)
                await msg.edit(embed=emb, view=view)
                return msg
            except discord.NotFound:
                pass
        msg = await target.send(embed=emb, view=view)
        if old_id and target.id != old_channel:   # moved to another channel: remove the old message
            old = guild.get_channel(old_channel)
            if old:
                try:
                    await (await old.fetch_message(old_id)).delete()
                except discord.HTTPException:
                    pass
        panel["channel"], panel["message_id"] = target.id, msg.id
        store.save(guild.id)
        return msg

    async def remove_panel(self, guild: discord.Guild, pid: str):
        panel = tcfg(guild.id)["panels"].pop(pid, None)
        store.save(guild.id)
        if panel and panel.get("message_id"):
            ch = guild.get_channel(panel["channel"])
            if ch:
                try:
                    await (await ch.fetch_message(panel["message_id"])).delete()
                except discord.HTTPException:
                    pass

    async def _log(self, guild: discord.Guild, emb: discord.Embed, file: discord.File = None):
        ch = guild.get_channel(tcfg(guild.id)["log_channel"] or 0)
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.send(embed=emb, file=file, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------ button handling
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type is not discord.InteractionType.component or not interaction.guild:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("tk:"):
            return
        parts = cid.split(":")
        try:
            if parts[1] == "open" and len(parts) == 4:
                await self.open_ticket(interaction, parts[2], parts[3])
            elif parts[1] == "close":
                await self.ask_close(interaction)
            elif parts[1] == "confirm":
                await self.confirm_close(interaction)
            elif parts[1] == "claim":
                await self.claim(interaction)
        except Exception:  # noqa: BLE001
            log.exception("Ticket interaction failed (%s)", cid)
            msg = "Something went wrong with that button. Please tell a moderator."
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except discord.HTTPException:
                pass

    async def open_ticket(self, interaction: discord.Interaction, pid: str, bid: str):
        await interaction.response.defer(ephemeral=True)
        guild, user = interaction.guild, interaction.user
        c = tcfg(guild.id)
        panel = c["panels"].get(pid)
        btn = next((b for b in panel["buttons"] if b["id"] == bid), None) if panel else None
        if not btn:
            return await interaction.followup.send("This button is no longer active.", ephemeral=True)

        mine = [ch for ch in guild.text_channels if ticket_owner(ch.topic) == user.id]
        if len(mine) >= c["max_open"]:
            where = ", ".join(ch.mention for ch in mine[:3])
            return await interaction.followup.send(f"You already have the maximum number of open tickets: {where}", ephemeral=True)

        category = guild.get_channel(btn["category"] or c["category"] or 0)
        roles = support_roles(guild, btn)
        allow = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True, embed_links=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: allow,
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, embed_links=True, manage_messages=True),
        }
        for role in roles:
            overwrites[role] = allow
        label = btn["label"] or "support"
        try:
            ch = await guild.create_text_channel(
                safe_channel_name(btn["name"], user.name, str(user.id)),
                category=category if isinstance(category, discord.CategoryChannel) else None,
                overwrites=overwrites, topic=f"ticket:{user.id}|{label}"[:1000], reason=f"Ticket opened by {user}")
        except discord.HTTPException:
            return await interaction.followup.send(
                "I couldn't create the ticket channel. Ask an admin to check that I have **Manage Channels** and that the category isn't full.",
                ephemeral=True)

        c["counter"] += 1
        number = c["counter"]
        store.save(guild.id)
        vals = dict(user=user.mention, username=user.display_name, server=guild.name, type=label, number=f"{number:04d}")
        emb = discord.Embed(title=fill(btn["welcome_title"], **vals) or None,
                            description=fill(btn["welcome_message"] or DEFAULT_WELCOME, **vals), color=btn["welcome_color"])
        emb.set_thumbnail(url=user.display_avatar.url)
        emb.set_footer(text=f"Ticket #{number:04d} · {label}")
        pings = [user.mention] + ([r.mention for r in roles] if btn["ping_staff"] else [])
        await ch.send(content=" ".join(pings), embed=emb, view=controls_view(),
                      allowed_mentions=discord.AllowedMentions(users=True, roles=True))
        await interaction.followup.send(f"✅ Your ticket is ready: {ch.mention}", ephemeral=True)
        await self._log(guild, embed(f"🎫 **{label}** ticket #{number:04d} opened by {user.mention} → {ch.mention}", color=0x57F287))

    def _ticket_check(self, interaction: discord.Interaction):
        """-> (owner id, error text). Only the owner or staff may act."""
        owner = ticket_owner(getattr(interaction.channel, "topic", None))
        if owner is None:
            return None, "This isn't a ticket channel."
        if interaction.user.id != owner and not is_staff(interaction.user):
            return None, "Only the ticket owner or staff can do that."
        return owner, None

    async def ask_close(self, interaction: discord.Interaction):
        owner, problem = self._ticket_check(interaction)
        if problem:
            return await interaction.response.send_message(problem, ephemeral=True)
        view = discord.ui.View(timeout=120)
        view.add_item(discord.ui.Button(label="Yes, close it", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="tk:confirm"))
        await interaction.response.send_message("Close this ticket? The channel will be deleted.", view=view, ephemeral=True)

    async def confirm_close(self, interaction: discord.Interaction):
        owner, problem = self._ticket_check(interaction)
        if problem:
            return await interaction.response.send_message(problem, ephemeral=True)
        await interaction.response.edit_message(content="🔒 Closing…", view=None)
        await self.close_channel(interaction.channel, interaction.user, owner)

    async def close_channel(self, channel: discord.TextChannel, closer: discord.Member, owner_id: int):
        try:
            await channel.send(embed=embed(f"🔒 Ticket closed by {closer.mention}. This channel will be deleted in 5 seconds."),
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass
        guild = channel.guild
        if tcfg(guild.id)["log_channel"]:
            lines = []
            async for m in channel.history(limit=1000, oldest_first=True):
                text = m.clean_content or ""
                if not text and m.embeds:
                    text = "[embed] " + " | ".join(x for x in (m.embeds[0].title, m.embeds[0].description) if x)[:300]
                text += "".join(f" [file: {a.url}]" for a in m.attachments)
                lines.append(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author}: {text}")
            file = discord.File(io.BytesIO("\n".join(lines).encode("utf-8")), filename=f"transcript-{channel.name}.txt")
            await self._log(guild, embed(f"🔒 **#{channel.name}** (owner <@{owner_id}>) closed by {closer.mention}. "
                                         f"{len(lines)} messages saved.", color=0xED4245), file=file)
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket closed by {closer}")
        except discord.HTTPException:
            pass

    async def claim(self, interaction: discord.Interaction):
        if ticket_owner(getattr(interaction.channel, "topic", None)) is None:
            return await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
        if not is_staff(interaction.user):
            return await interaction.response.send_message("Only ticket staff can claim tickets.", ephemeral=True)
        msg = interaction.message
        emb = msg.embeds[0].copy() if msg.embeds else discord.Embed(color=BLURPLE)
        emb.add_field(name="Claimed by", value=interaction.user.mention, inline=False)
        await interaction.response.edit_message(embed=emb, view=controls_view(claimed=True))
        await interaction.channel.send(embed=embed(f"🙋 {interaction.user.mention} will help you with this ticket."),
                                       allowed_mentions=discord.AllowedMentions.none())

    # ------------------------------------------------------------------ commands: overview + general settings
    @commands.group(invoke_without_command=True, help="Tickets: setup, panel, button, supportrole, category, logchannel, maxopen, add, remove, close")
    async def ticket(self, ctx):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=err("You need Manage Server to view ticket settings."))
        c, p, g = tcfg(ctx.guild.id), ctx.clean_prefix, ctx.guild
        role, cat, logc = g.get_role(c["support_role"] or 0), g.get_channel(c["category"] or 0), g.get_channel(c["log_channel"] or 0)
        await ctx.send(embed=embed(
            f"**Support role:** {role.mention if role else 'not set'}\n**Category:** {cat.name if cat else 'not set'}\n"
            f"**Log channel:** {logc.mention if logc else 'not set'}\n**Max open per member:** {c['max_open']}\n"
            f"**Panels:** {len(c['panels'])}\n\n"
            f"**Quick start:** `{p}ticket supportrole @Staff` then `{p}ticket setup #channel`\n"
            f"`{p}ticket category <category>` · `{p}ticket logchannel #logs` · `{p}ticket maxopen 2`\n"
            f"`{p}ticket panel list|create|send|set|delete`\n`{p}ticket button add|set|remove`\n"
            f"`{p}ticket add @user` · `{p}ticket remove @user` · `{p}ticket close`\n\n"
            f"Ticket message placeholders: `{PLACEHOLDERS}`\n"
            f"Everything can also be edited on the website dashboard.", title="Tickets"), allowed_mentions=discord.AllowedMentions.none())

    @ticket.command(name="supportrole", help="Role that can see and answer every ticket.")
    @commands.has_permissions(manage_guild=True)
    async def t_support(self, ctx, role: discord.Role):
        tcfg(ctx.guild.id)["support_role"] = role.id
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Support role set to {role.mention}."), allowed_mentions=discord.AllowedMentions.none())

    @ticket.command(name="category", help="Category where tickets are created.")
    @commands.has_permissions(manage_guild=True)
    async def t_category(self, ctx, category: discord.CategoryChannel):
        tcfg(ctx.guild.id)["category"] = category.id
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Tickets will be created in **{category.name}**."))

    @ticket.command(name="logchannel", help="Channel for ticket logs + transcripts (no channel = turn off).")
    @commands.has_permissions(manage_guild=True)
    async def t_log(self, ctx, channel: discord.TextChannel = None):
        tcfg(ctx.guild.id)["log_channel"] = channel.id if channel else None
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Ticket logs go to {channel.mention}." if channel else "Ticket logs are off."))

    @ticket.command(name="maxopen", help="How many tickets one member can have open (1-10).")
    @commands.has_permissions(manage_guild=True)
    async def t_maxopen(self, ctx, number: int):
        if not 1 <= number <= 10:
            return await ctx.send(embed=err("Choose 1 to 10."))
        tcfg(ctx.guild.id)["max_open"] = number
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Each member can have **{number}** open ticket(s)."))

    @ticket.command(name="setup", help="Quick start: create a panel and post it in a channel.")
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(manage_channels=True, embed_links=True)
    async def t_setup(self, ctx, channel: discord.TextChannel):
        c = tcfg(ctx.guild.id)
        if len(c["panels"]) >= MAX_PANELS:
            return await ctx.send(embed=err(f"You already have {MAX_PANELS} panels. Delete one first."))
        pid, c["next_panel"] = next_id("p", c["next_panel"], c["panels"])
        c["panels"][pid] = new_panel(channel.id)
        await self.publish_panel(ctx.guild, pid)
        p = ctx.clean_prefix
        await ctx.send(embed=ok(f"Panel `{pid}` posted in {channel.mention}. Customize it with `{p}ticket panel set {pid} title …`, "
                                f"`{p}ticket button add {pid} 🛒 Billing`, or on the website."))

    # ------------------------------------------------------------------ commands: panels
    @ticket.group(name="panel", invoke_without_command=True, help="Manage panels: list, create, send, set, delete")
    @commands.has_permissions(manage_guild=True)
    async def t_panel(self, ctx):
        p = ctx.clean_prefix
        await ctx.send(embed=embed(
            f"`{p}ticket panel list`\n`{p}ticket panel create #channel`\n`{p}ticket panel send <id> [#channel]` — post or update the message\n"
            f"`{p}ticket panel set <id> <title|description|color|image|thumbnail|footer> <value>` (use `none` to clear)\n"
            f"`{p}ticket panel delete <id>`", title="Ticket panels"))

    def _get_panel(self, ctx, pid):
        panel = tcfg(ctx.guild.id)["panels"].get(pid.lower())
        return panel, (None if panel else f"No panel `{pid}`. See `{ctx.clean_prefix}ticket panel list`.")

    async def _refresh(self, ctx, pid):
        panel = tcfg(ctx.guild.id)["panels"][pid]
        if panel.get("message_id"):
            try:
                await self.publish_panel(ctx.guild, pid)
            except (discord.HTTPException, ValueError):
                pass

    @t_panel.command(name="list", help="Show all panels.")
    async def tp_list(self, ctx):
        panels = tcfg(ctx.guild.id)["panels"]
        lines = [f"`{pid}` → <#{p['channel']}> · {len(p['buttons'])} button(s) · {'posted ✅' if p['message_id'] else 'not posted yet'}"
                 for pid, p in panels.items()]
        await ctx.send(embed=embed("\n".join(lines) or "No panels yet. Use `ticket setup #channel`.", title="Ticket panels"))

    @t_panel.command(name="create", help="Create and post a new panel.")
    @commands.bot_has_permissions(embed_links=True)
    async def tp_create(self, ctx, channel: discord.TextChannel):
        await self.t_setup(ctx, channel)

    @t_panel.command(name="send", help="Post the panel (or update the posted message), optionally in another channel.")
    @commands.bot_has_permissions(embed_links=True)
    async def tp_send(self, ctx, panel_id: str, channel: discord.TextChannel = None):
        panel, problem = self._get_panel(ctx, panel_id)
        if problem:
            return await ctx.send(embed=err(problem))
        try:
            msg = await self.publish_panel(ctx.guild, panel_id.lower(), channel)
        except ValueError as exc:
            return await ctx.send(embed=err(str(exc)))
        await ctx.send(embed=ok(f"Panel posted: {msg.jump_url}"))

    @t_panel.command(name="delete", help="Delete a panel and its message.")
    async def tp_delete(self, ctx, panel_id: str):
        panel, problem = self._get_panel(ctx, panel_id)
        if problem:
            return await ctx.send(embed=err(problem))
        await self.remove_panel(ctx.guild, panel_id.lower())
        await ctx.send(embed=ok(f"Panel `{panel_id}` deleted."))

    @t_panel.command(name="set", help="Edit a panel: set <id> <title|description|color|image|thumbnail|footer> <value>")
    async def tp_set(self, ctx, panel_id: str, field: str, *, value: str):
        panel, problem = self._get_panel(ctx, panel_id)
        if problem:
            return await ctx.send(embed=err(problem))
        field, clear = field.lower(), value.strip().lower() == "none"
        if field in ("title", "description", "footer"):
            limit = {"title": 256, "description": 4000, "footer": 2048}[field]
            if len(value) > limit:
                return await ctx.send(embed=err(f"Max {limit} characters."))
            panel[field] = "" if clear else value
        elif field == "color":
            color = parse_color(value)
            if color is None or not 0 <= color <= 0xFFFFFF:
                return await ctx.send(embed=err("Give a hex color like `#ff66aa`."))
            panel["color"] = color
        elif field in ("image", "thumbnail"):
            if not clear and not value.lower().startswith(("http://", "https://")):
                return await ctx.send(embed=err("Give a link starting with http:// or https://"))
            panel[field] = "" if clear else value
        else:
            return await ctx.send(embed=err("Fields: title, description, color, image, thumbnail, footer."))
        store.save(ctx.guild.id)
        await self._refresh(ctx, panel_id.lower())
        await ctx.send(embed=ok(f"Panel `{panel_id}` {field} updated."))

    # ------------------------------------------------------------------ commands: buttons
    @ticket.group(name="button", invoke_without_command=True, help="Manage panel buttons: add, set, remove")
    @commands.has_permissions(manage_guild=True)
    async def t_button(self, ctx):
        p = ctx.clean_prefix
        await ctx.send(embed=embed(
            f"`{p}ticket button add <panel> <emoji> <label>` — e.g. `{p}ticket button add p1 🛒 Billing`\n"
            f"`{p}ticket button set <panel> <number> <field> <value>`\n"
            f"Fields: `label` `emoji` `style` (blurple/green/red/grey) `name` `category` `role` `ping` (on/off) "
            f"`welcome_title` `welcome` `welcome_color`\n"
            f"`{p}ticket button remove <panel> <number>`\n\n"
            f"Emoji can be a normal emoji, a server emoji like `:name:`, or `<:name:id>`.\n"
            f"Welcome placeholders: `{PLACEHOLDERS}`", title="Ticket buttons"))

    @t_button.command(name="add", help="Add a button: add <panel> <emoji|none> <label>")
    async def tb_add(self, ctx, panel_id: str, emoji: str, *, label: str):
        panel, problem = self._get_panel(ctx, panel_id)
        if problem:
            return await ctx.send(embed=err(problem))
        if len(panel["buttons"]) >= MAX_BUTTONS:
            return await ctx.send(embed=err(f"A panel can have at most {MAX_BUTTONS} buttons."))
        try:
            emoji = normalize_emoji(emoji, server_emojis(ctx.guild))
        except ValueError as exc:
            return await ctx.send(embed=err(str(exc)))
        if len(label) > 80:
            return await ctx.send(embed=err("Label is too long (max 80)."))
        bid, panel["next_button"] = next_id("b", panel["next_button"], [b["id"] for b in panel["buttons"]])
        btn = new_button(bid, label=label, emoji=emoji)
        btn["name"] = label.split()[0] if label.split() else "ticket"
        panel["buttons"].append(btn)
        store.save(ctx.guild.id)
        await self._refresh(ctx, panel_id.lower())
        await ctx.send(embed=ok(f"Button **{label}** added as number {len(panel['buttons'])}."))

    @t_button.command(name="remove", help="Remove a button by its number.")
    async def tb_remove(self, ctx, panel_id: str, number: int):
        panel, problem = self._get_panel(ctx, panel_id)
        if problem:
            return await ctx.send(embed=err(problem))
        if len(panel["buttons"]) <= 1:
            return await ctx.send(embed=err("A panel needs at least one button. Delete the panel instead."))
        if not 1 <= number <= len(panel["buttons"]):
            return await ctx.send(embed=err(f"Choose 1-{len(panel['buttons'])}."))
        removed = panel["buttons"].pop(number - 1)
        store.save(ctx.guild.id)
        await self._refresh(ctx, panel_id.lower())
        await ctx.send(embed=ok(f"Removed button **{removed['label'] or removed['emoji']}**."))

    @t_button.command(name="set", help="Edit a button: set <panel> <number> <field> <value>")
    async def tb_set(self, ctx, panel_id: str, number: int, field: str, *, value: str):
        panel, problem = self._get_panel(ctx, panel_id)
        if problem:
            return await ctx.send(embed=err(problem))
        if not 1 <= number <= len(panel["buttons"]):
            return await ctx.send(embed=err(f"Choose a button number from 1 to {len(panel['buttons'])}."))
        btn, field, clear = panel["buttons"][number - 1], field.lower(), value.strip().lower() == "none"
        try:
            if field == "label":
                if clear and not btn["emoji"]:
                    raise ValueError("A button needs a label or an emoji.")
                if len(value) > 80:
                    raise ValueError("Label is too long (max 80).")
                btn["label"] = "" if clear else value
            elif field == "emoji":
                new = normalize_emoji(value, server_emojis(ctx.guild))
                if not new and not btn["label"]:
                    raise ValueError("A button needs a label or an emoji.")
                btn["emoji"] = new
            elif field == "style":
                if value.lower() not in STYLE_NAMES:
                    raise ValueError("Style: " + ", ".join(STYLE_NAMES))
                btn["style"] = value.lower()
            elif field == "name":
                btn["name"] = clean_channel_part(value)[:30] or "ticket"
            elif field == "category":
                btn["category"] = None if clear else (await commands.CategoryChannelConverter().convert(ctx, value)).id
            elif field in ("role", "support_role"):
                btn["support_role"] = None if clear else (await commands.RoleConverter().convert(ctx, value)).id
            elif field == "ping":
                if value.lower() not in ("on", "off"):
                    raise ValueError("Use `on` or `off`.")
                btn["ping_staff"] = value.lower() == "on"
            elif field == "welcome_title":
                btn["welcome_title"] = "" if clear else value[:256]
            elif field in ("welcome", "welcome_message"):
                btn["welcome_message"] = DEFAULT_WELCOME if clear else value[:2000]
            elif field == "welcome_color":
                color = parse_color(value)
                if color is None or not 0 <= color <= 0xFFFFFF:
                    raise ValueError("Give a hex color like `#ff66aa`.")
                btn["welcome_color"] = color
            else:
                raise ValueError("Fields: label, emoji, style, name, category, role, ping, welcome_title, welcome, welcome_color.")
        except (ValueError, commands.BadArgument) as exc:
            return await ctx.send(embed=err(str(exc)))
        store.save(ctx.guild.id)
        await self._refresh(ctx, panel_id.lower())
        await ctx.send(embed=ok(f"Button {number} {field} updated."))

    # ------------------------------------------------------------------ commands: inside a ticket
    def _in_ticket(self, ctx):
        return ticket_owner(ctx.channel.topic) is not None

    @ticket.command(name="add", help="Add a member to this ticket.")
    async def t_add(self, ctx, member: discord.Member):
        if not self._in_ticket(ctx):
            return await ctx.send(embed=err("Use this inside a ticket channel."))
        if not is_staff(ctx.author):
            return await ctx.send(embed=err("Only ticket staff can do that."))
        await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
        await ctx.send(embed=ok(f"Added {member.mention} to the ticket."))

    @ticket.command(name="remove", help="Remove a member from this ticket.")
    async def t_remove(self, ctx, member: discord.Member):
        if not self._in_ticket(ctx):
            return await ctx.send(embed=err("Use this inside a ticket channel."))
        if not is_staff(ctx.author):
            return await ctx.send(embed=err("Only ticket staff can do that."))
        if ticket_owner(ctx.channel.topic) == member.id:
            return await ctx.send(embed=err("You can't remove the ticket owner."))
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.send(embed=ok(f"Removed {member.mention} from the ticket."))

    @ticket.command(name="close", help="Close this ticket.")
    async def t_close(self, ctx):
        owner = ticket_owner(ctx.channel.topic)
        if owner is None:
            return await ctx.send(embed=err("Use this inside a ticket channel."))
        if ctx.author.id != owner and not is_staff(ctx.author):
            return await ctx.send(embed=err("Only the ticket owner or staff can close this."))
        await self.close_channel(ctx.channel, ctx.author, owner)


async def setup(bot):
    await bot.add_cog(Tickets(bot))
