import copy
import random
import time

import discord
from discord.ext import commands, tasks

from utils.helpers import BLURPLE, ok, err, embed, parse_color
from utils.levelmath import bar, compact, fill, level_from_xp, progress, total_xp_for
from utils.store import levels, store

DEFAULTS = {
    "enabled": False,
    "announce": "same",            # same | channel | dm | off
    "channel": None,
    "message": "GG {user}, you just reached **level {level}**! 🎉",
    "embed": True,
    "title": "⬆️ Level up!",
    "color": BLURPLE,
    "image": "",
    "footer": "",
    "xp_min": 15,
    "xp_max": 25,
    "cooldown": 60,
    "ignored_channels": [],
    "ignored_roles": [],
    "rewards": {},                 # {"5": role_id}
    "stack_rewards": True,
}
ANNOUNCE_MODES = ("same", "channel", "dm", "off")
PLACEHOLDERS = "{user} {username} {level} {xp} {server}"


def lv_cfg(gid) -> dict:
    """The server's leveling settings (missing keys are filled with defaults)."""
    c = store.get(gid).setdefault("leveling", {})
    for key, val in DEFAULTS.items():
        c.setdefault(key, copy.deepcopy(val))
    return c


def lv_users(gid) -> dict:
    return levels.get(gid).setdefault("users", {})


def ranking(gid):
    return sorted(lv_users(gid).items(), key=lambda kv: kv[1].get("xp", 0), reverse=True)


def build_levelup(member: discord.Member, level: int, xp: int, c: dict):
    """-> (text or None, embed or None) for a level-up announcement."""
    vals = dict(user=member.mention, username=member.display_name, level=level, xp=compact(xp), server=member.guild.name)
    text = fill(c["message"], **vals)
    if not c["embed"]:
        return text, None
    emb = discord.Embed(title=fill(c["title"], **vals) or None, description=text, color=c["color"])
    emb.set_thumbnail(url=member.display_avatar.url)
    if c["image"]:
        emb.set_image(url=c["image"])
    if c["footer"]:
        emb.set_footer(text=fill(c["footer"], **vals))
    return None, emb


class BoardView(discord.ui.View):
    def __init__(self, author_id, rows, guild_name, color):
        super().__init__(timeout=120)
        self.author_id, self.rows, self.guild_name, self.color = author_id, rows, guild_name, color
        self.page, self.pages, self.message = 0, max(1, (len(rows) + 9) // 10), None
        self._sync()

    def make(self) -> discord.Embed:
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, (member, xp) in enumerate(self.rows[self.page * 10:(self.page + 1) * 10], start=self.page * 10 + 1):
            lines.append(f"{medals.get(i, f'`#{i}`')} {member.mention} · **Lv {level_from_xp(xp)}** · {compact(xp)} XP")
        emb = discord.Embed(title=f"🏆 {self.guild_name} leaderboard", description="\n".join(lines) or "Nobody has XP yet.", color=self.color)
        emb.set_footer(text=f"Page {self.page + 1}/{self.pages} · {len(self.rows)} ranked")
        return emb

    def _sync(self):
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= self.pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Run the command yourself to flip pages.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.make(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync()
        await interaction.response.edit_message(embed=self.make(), view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Leveling(commands.Cog, name="Leveling"):
    """XP for chatting, ranks, a leaderboard, role rewards and custom level-up embeds."""

    def __init__(self, bot):
        self.bot = bot
        self.last = {}  # (guild, user) -> last time XP was given

    async def cog_load(self):
        self.flusher.start()

    async def cog_unload(self):
        self.flusher.cancel()
        levels.flush_dirty()

    @tasks.loop(seconds=30)
    async def flusher(self):
        levels.flush_dirty()
        cutoff = time.time() - 3600
        self.last = {k: t for k, t in self.last.items() if t > cutoff}

    # ------------------------------------------------------------ giving XP
    async def level_up(self, member: discord.Member, channel, level: int, xp: int, c: dict):
        await self.apply_rewards(member, level, c)
        mode = c["announce"]
        if mode == "off":
            return
        text, emb = build_levelup(member, level, xp, c)
        target = channel
        if mode == "channel" and c["channel"]:
            target = member.guild.get_channel(c["channel"]) or channel
        if mode == "dm":
            target = member
        if target is None:
            return
        try:
            await target.send(content=text, embed=emb, allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.HTTPException:
            pass

    async def apply_rewards(self, member: discord.Member, level: int, c: dict):
        wanted = {int(l): rid for l, rid in c["rewards"].items() if int(l) <= level}
        if not wanted:
            return
        guild = member.guild
        targets = list(wanted.values()) if c["stack_rewards"] else [wanted[max(wanted)]]

        def usable(role):
            return role and not role.managed and role < guild.me.top_role

        add = [r for r in map(guild.get_role, targets) if usable(r) and r not in member.roles]
        remove = []
        if not c["stack_rewards"]:
            remove = [r for r in map(guild.get_role, c["rewards"].values()) if usable(r) and r in member.roles and r.id not in targets]
        try:
            if add:
                await member.add_roles(*add, reason="Level reward")
            if remove:
                await member.remove_roles(*remove, reason="Level reward (replaced by a higher one)")
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return
        c = lv_cfg(message.guild.id)
        if not c["enabled"] or message.channel.id in c["ignored_channels"]:
            return
        member = message.author
        if c["ignored_roles"] and any(r.id in c["ignored_roles"] for r in member.roles):
            return
        if (await self.bot.get_context(message)).valid:  # don't reward commands
            return
        key, now = (message.guild.id, member.id), time.time()
        if now - self.last.get(key, 0) < c["cooldown"]:
            return
        self.last[key] = now

        user = lv_users(message.guild.id).setdefault(str(member.id), {"xp": 0})
        old = user["xp"]
        user["xp"] = old + random.randint(c["xp_min"], max(c["xp_min"], c["xp_max"]))
        levels.touch(message.guild.id)
        new_level = level_from_xp(user["xp"])
        if new_level > level_from_xp(old):
            await self.level_up(member, message.channel, new_level, user["xp"], c)

    # ------------------------------------------------------------ member commands
    @commands.command(aliases=["level", "lvl", "xp"], help="Show your (or someone's) level and rank.")
    async def rank(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        xp = lv_users(ctx.guild.id).get(str(member.id), {}).get("xp", 0)
        level, into, need = progress(xp)
        order = [uid for uid, _ in ranking(ctx.guild.id)]
        pos = f"#{order.index(str(member.id)) + 1}" if str(member.id) in order else "unranked"
        emb = discord.Embed(color=lv_cfg(ctx.guild.id)["color"])
        emb.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        emb.set_thumbnail(url=member.display_avatar.url)
        emb.add_field(name="Level", value=f"**{level}**")
        emb.add_field(name="Rank", value=f"**{pos}**")
        emb.add_field(name="Total XP", value=f"**{compact(xp)}**")
        emb.add_field(name=f"Progress to level {level + 1}", value=f"{bar(into, need, 14)}\n`{into:,} / {need:,} XP`", inline=False)
        await ctx.send(embed=emb)

    @commands.command(aliases=["lb", "top", "levels"], help="Server XP leaderboard.")
    async def leaderboard(self, ctx):
        rows = [(m, d.get("xp", 0)) for uid, d in ranking(ctx.guild.id) if (m := ctx.guild.get_member(int(uid))) and d.get("xp", 0) > 0]
        view = BoardView(ctx.author.id, rows, ctx.guild.name, lv_cfg(ctx.guild.id)["color"])
        view.message = await ctx.send(embed=view.make(), view=view if view.pages > 1 else None,
                                      allowed_mentions=discord.AllowedMentions.none())

    # ------------------------------------------------------------ admin commands
    @commands.group(aliases=["lvlset"], invoke_without_command=True,
                    help="Leveling settings. Run it with no arguments to see everything you can change.")
    @commands.has_permissions(manage_guild=True)
    async def leveling(self, ctx):
        c, p = lv_cfg(ctx.guild.id), ctx.clean_prefix
        dest = {"same": "the channel they chatted in", "channel": f"<#{c['channel']}>" if c["channel"] else "the channel they chatted in",
                "dm": "their DMs", "off": "nowhere (silent)"}[c["announce"]]
        emb = embed(title="Leveling settings", color=c["color"], desc=(
            f"**Status:** {'✅ on' if c['enabled'] else '❌ off'}\n"
            f"**Level-ups go to:** {dest}\n"
            f"**XP per message:** {c['xp_min']}–{c['xp_max']} every {c['cooldown']}s\n"
            f"**Rewards:** {len(c['rewards'])} · **Ignored:** {len(c['ignored_channels'])} channels, {len(c['ignored_roles'])} roles\n\n"
            f"`{p}leveling on|off` · `channel [#ch]` · `announce same|channel|dm|off`\n"
            f"`message <text>` · `embed on|off` · `title <text>` · `color <hex>` · `image <url|none>` · `footer <text|none>`\n"
            f"`xp <min> <max>` · `cooldown <seconds>` · `ignore channel|role <target>`\n"
            f"`reward add <level> @role` · `reward remove <level>` · `reward list` · `reward stack on|off`\n"
            f"`preview` · `setlevel @user <n>` · `addxp @user <n>` · `resetxp @user`\n\n"
            f"Placeholders for message/title/footer: `{PLACEHOLDERS}`"))
        await ctx.send(embed=emb)

    def _save(self, ctx):
        store.save(ctx.guild.id)

    @leveling.command(name="on", help="Turn XP gain on.")
    async def lv_on(self, ctx):
        lv_cfg(ctx.guild.id)["enabled"] = True
        self._save(ctx)
        await ctx.send(embed=ok("Leveling is **on**. Members earn XP when they chat."))

    @leveling.command(name="off", help="Turn XP gain off (XP is kept).")
    async def lv_off(self, ctx):
        lv_cfg(ctx.guild.id)["enabled"] = False
        self._save(ctx)
        await ctx.send(embed=ok("Leveling is **off**. Existing XP is kept."))

    @leveling.command(name="channel", help="Send level-ups to a channel (no channel = where they chatted).")
    async def lv_channel(self, ctx, channel: discord.TextChannel = None):
        c = lv_cfg(ctx.guild.id)
        c["channel"], c["announce"] = (channel.id, "channel") if channel else (None, "same")
        self._save(ctx)
        await ctx.send(embed=ok(f"Level-ups go to {channel.mention}." if channel else "Level-ups go to the channel members chat in."))

    @leveling.command(name="announce", help="Where to announce: same, channel, dm or off.")
    async def lv_announce(self, ctx, mode: str):
        mode = mode.lower()
        if mode not in ANNOUNCE_MODES:
            return await ctx.send(embed=err("Choose: same, channel, dm or off."))
        lv_cfg(ctx.guild.id)["announce"] = mode
        self._save(ctx)
        await ctx.send(embed=ok(f"Announce mode: **{mode}**."))

    @leveling.command(name="message", help="Level-up text. Placeholders: " + PLACEHOLDERS)
    async def lv_message(self, ctx, *, text: str):
        if len(text) > 1500:
            return await ctx.send(embed=err("Keep it under 1500 characters."))
        lv_cfg(ctx.guild.id)["message"] = text
        self._save(ctx)
        await ctx.send(embed=ok("Level-up message updated. Try `preview`."))

    @leveling.command(name="embed", help="Send level-ups as an embed (on) or plain text (off).")
    async def lv_embed(self, ctx, mode: str):
        if mode.lower() not in ("on", "off"):
            return await ctx.send(embed=err("Use `on` or `off`."))
        lv_cfg(ctx.guild.id)["embed"] = mode.lower() == "on"
        self._save(ctx)
        await ctx.send(embed=ok(f"Embed level-ups **{mode.lower()}**."))

    @leveling.command(name="title", help="Embed title. Use `none` to remove.")
    async def lv_title(self, ctx, *, text: str):
        lv_cfg(ctx.guild.id)["title"] = "" if text.lower() == "none" else text[:256]
        self._save(ctx)
        await ctx.send(embed=ok("Title updated."))

    @leveling.command(name="color", help="Embed color, e.g. #ff66aa")
    async def lv_color(self, ctx, color: str):
        value = parse_color(color)
        if value is None:
            return await ctx.send(embed=err("Give a hex color like `#ff66aa`."))
        lv_cfg(ctx.guild.id)["color"] = value
        self._save(ctx)
        await ctx.send(embed=ok(f"Color set to `#{value:06x}`."))

    @leveling.command(name="image", help="Big image in the level-up embed (link, or `none`).")
    async def lv_image(self, ctx, url: str):
        if url.lower() == "none":
            url = ""
        elif not url.lower().startswith(("http://", "https://")):
            return await ctx.send(embed=err("Give a link starting with http:// or https://"))
        lv_cfg(ctx.guild.id)["image"] = url
        self._save(ctx)
        await ctx.send(embed=ok("Image updated."))

    @leveling.command(name="footer", help="Embed footer text (or `none`).")
    async def lv_footer(self, ctx, *, text: str):
        lv_cfg(ctx.guild.id)["footer"] = "" if text.lower() == "none" else text[:2048]
        self._save(ctx)
        await ctx.send(embed=ok("Footer updated."))

    @leveling.command(name="xp", help="XP range per message: xp <min> <max>")
    async def lv_xp(self, ctx, low: int, high: int):
        if not (1 <= low <= high <= 500):
            return await ctx.send(embed=err("Use numbers from 1 to 500 with min ≤ max."))
        c = lv_cfg(ctx.guild.id)
        c["xp_min"], c["xp_max"] = low, high
        self._save(ctx)
        await ctx.send(embed=ok(f"XP per message: **{low}–{high}**."))

    @leveling.command(name="cooldown", help="Seconds between XP gains per member (0-3600).")
    async def lv_cooldown(self, ctx, seconds: int):
        if not 0 <= seconds <= 3600:
            return await ctx.send(embed=err("Choose 0 to 3600 seconds."))
        lv_cfg(ctx.guild.id)["cooldown"] = seconds
        self._save(ctx)
        await ctx.send(embed=ok(f"Cooldown: **{seconds}s**."))

    @leveling.command(name="ignore", help="Toggle ignoring a channel or role: ignore channel #x | ignore role @y")
    async def lv_ignore(self, ctx, kind: str, target: str):
        kind = kind.lower()
        try:
            if kind == "channel":
                obj, key = await commands.TextChannelConverter().convert(ctx, target), "ignored_channels"
            elif kind == "role":
                obj, key = await commands.RoleConverter().convert(ctx, target), "ignored_roles"
            else:
                return await ctx.send(embed=err("Use `ignore channel #name` or `ignore role @name`."))
        except commands.BadArgument as exc:
            return await ctx.send(embed=err(str(exc)))
        lst = lv_cfg(ctx.guild.id)[key]
        if obj.id in lst:
            lst.remove(obj.id)
            msg = f"{obj.mention} earns XP again."
        else:
            lst.append(obj.id)
            msg = f"{obj.mention} no longer earns XP."
        self._save(ctx)
        await ctx.send(embed=ok(msg))

    @leveling.group(name="reward", invoke_without_command=True, help="Role rewards: reward add <level> @role | remove <level> | list | stack on|off")
    async def lv_reward(self, ctx):
        await ctx.send(embed=embed(f"`{ctx.clean_prefix}leveling reward add <level> @role`\n`… remove <level>`\n`… list`\n`… stack on|off`", title="Level rewards"))

    @lv_reward.command(name="add", help="Give a role at a level.")
    @commands.bot_has_permissions(manage_roles=True)
    async def lv_reward_add(self, ctx, level: int, *, role: discord.Role):
        if not 1 <= level <= 500:
            return await ctx.send(embed=err("Level must be 1-500."))
        if role.is_default() or role.managed or role >= ctx.guild.me.top_role:
            return await ctx.send(embed=err("I can't give that role (it must be below my highest role and not managed)."))
        if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
            return await ctx.send(embed=err("That role is above your highest role."))
        lv_cfg(ctx.guild.id)["rewards"][str(level)] = role.id
        self._save(ctx)
        await ctx.send(embed=ok(f"Reaching level **{level}** now gives {role.mention}."))

    @lv_reward.command(name="remove", help="Remove the reward for a level.")
    async def lv_reward_remove(self, ctx, level: int):
        if lv_cfg(ctx.guild.id)["rewards"].pop(str(level), None) is None:
            return await ctx.send(embed=err("No reward at that level."))
        self._save(ctx)
        await ctx.send(embed=ok(f"Removed the level {level} reward."))

    @lv_reward.command(name="list", help="Show all rewards.")
    async def lv_reward_list(self, ctx):
        rewards = lv_cfg(ctx.guild.id)["rewards"]
        lines = [f"Level **{l}** → <@&{rid}>" for l, rid in sorted(rewards.items(), key=lambda kv: int(kv[0]))]
        await ctx.send(embed=embed("\n".join(lines) or "No rewards yet.", title="Level rewards"), allowed_mentions=discord.AllowedMentions.none())

    @lv_reward.command(name="stack", help="on = keep every earned reward, off = only the highest one.")
    async def lv_reward_stack(self, ctx, mode: str):
        if mode.lower() not in ("on", "off"):
            return await ctx.send(embed=err("Use `on` or `off`."))
        lv_cfg(ctx.guild.id)["stack_rewards"] = mode.lower() == "on"
        self._save(ctx)
        await ctx.send(embed=ok(f"Reward stacking **{mode.lower()}**."))

    @leveling.command(name="preview", help="Show a sample level-up message.")
    async def lv_preview(self, ctx):
        c = lv_cfg(ctx.guild.id)
        xp = lv_users(ctx.guild.id).get(str(ctx.author.id), {}).get("xp", 0)
        level = max(1, level_from_xp(xp))
        text, emb = build_levelup(ctx.author, level, max(xp, total_xp_for(level)), c)
        await ctx.send(content=text, embed=emb, allowed_mentions=discord.AllowedMentions.none())

    async def _set_xp(self, ctx, member, xp):
        xp = max(0, xp)
        lv_users(ctx.guild.id).setdefault(str(member.id), {"xp": 0})["xp"] = xp
        self._save_levels(ctx)
        await self.apply_rewards(member, level_from_xp(xp), lv_cfg(ctx.guild.id))
        return xp

    def _save_levels(self, ctx):
        levels.save(ctx.guild.id)

    @leveling.command(name="setlevel", help="Set a member's level.")
    async def lv_setlevel(self, ctx, member: discord.Member, level: int):
        if not 0 <= level <= 500:
            return await ctx.send(embed=err("Level must be 0-500."))
        await self._set_xp(ctx, member, total_xp_for(level))
        await ctx.send(embed=ok(f"{member.mention} is now level **{level}**."), allowed_mentions=discord.AllowedMentions.none())

    @leveling.command(name="addxp", help="Add (or subtract) XP for a member.")
    async def lv_addxp(self, ctx, member: discord.Member, amount: int):
        cur = lv_users(ctx.guild.id).get(str(member.id), {}).get("xp", 0)
        new = await self._set_xp(ctx, member, cur + amount)
        await ctx.send(embed=ok(f"{member.mention} now has **{new:,} XP** (level {level_from_xp(new)})."), allowed_mentions=discord.AllowedMentions.none())

    @leveling.command(name="resetxp", help="Reset a member's XP to 0.")
    async def lv_resetxp(self, ctx, member: discord.Member):
        lv_users(ctx.guild.id).pop(str(member.id), None)
        self._save_levels(ctx)
        await ctx.send(embed=ok(f"Reset {member.mention}'s XP."), allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Leveling(bot))
