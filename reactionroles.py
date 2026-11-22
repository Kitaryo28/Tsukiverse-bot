import discord
from discord.ext import commands

from utils.helpers import ok, err, embed, role_error
from utils.store import store


def emoji_key(emoji) -> str:
    """Stable key for an emoji: the ID for custom emoji, the character for unicode ones."""
    return str(emoji.id) if getattr(emoji, "id", None) else str(emoji.name)


def parse_emoji(text: str) -> discord.PartialEmoji:
    text = (text or "").strip()
    if not text or len(text) > 64 or any(c.isspace() for c in text):
        raise ValueError("Give a single emoji.")
    return discord.PartialEmoji.from_str(text)


def rr_data(gid) -> dict:
    return store.get(gid).setdefault("reactionroles", {})


class ReactionRoles(commands.Cog, name="ReactionRoles"):
    """Give roles when members react to a message."""

    def __init__(self, bot):
        self.bot = bot

    # ---------- shared helpers (also used by the website) ----------
    async def attach(self, message: discord.Message, pairs):
        """Link emoji -> role on a message and add the bot's reactions.

        pairs: list of (PartialEmoji, Role). Raises ValueError with a readable reason.
        """
        if not pairs:
            raise ValueError("Add at least one emoji + role.")
        entry = rr_data(message.guild.id).get(str(message.id), {"channel": message.channel.id, "roles": {}})
        if len(entry["roles"]) + len(pairs) > 20:
            raise ValueError("A message can have at most 20 reaction roles.")
        for emoji, role in pairs:
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                raise ValueError(f"I can't use the emoji {emoji} (is it from a server I'm not in?).")
            entry["roles"][emoji_key(emoji)] = {"emoji": str(emoji), "role": role.id}
        entry["channel"] = message.channel.id
        rr_data(message.guild.id)[str(message.id)] = entry
        store.save(message.guild.id)

    async def detach(self, guild: discord.Guild, message_id: int, emoji: discord.PartialEmoji = None) -> bool:
        data = rr_data(guild.id)
        entry = data.get(str(message_id))
        if not entry:
            return False
        channel = guild.get_channel(entry.get("channel", 0))
        if emoji is None:
            data.pop(str(message_id))
        elif entry["roles"].pop(emoji_key(emoji), None) is None:
            return False
        elif not entry["roles"]:
            data.pop(str(message_id))
        store.save(guild.id)
        if channel:  # tidy up the bot's own reactions (best effort)
            try:
                msg = await channel.fetch_message(message_id)
                targets = [emoji] if emoji else [discord.PartialEmoji.from_str(r["emoji"]) for r in entry["roles"].values()]
                for e in targets:
                    await msg.remove_reaction(e, guild.me)
            except discord.HTTPException:
                pass
        return True

    # ---------- commands ----------
    @commands.group(aliases=["rr"], invoke_without_command=True,
                    help="Reaction roles: reactionrole add <message> <emoji> <role> | remove <message> [emoji] | list")
    @commands.has_permissions(manage_roles=True)
    async def reactionrole(self, ctx):
        p = ctx.clean_prefix
        await ctx.send(embed=embed(
            f"`{p}reactionrole add <message id or link> <emoji> <role>`\n"
            f"`{p}reactionrole remove <message id or link> [emoji]`\n"
            f"`{p}reactionrole list`\n\n"
            "Tip: send the message first (e.g. with the `embed` command), then link roles to it. "
            "You can also set this up from the website dashboard.",
            title="Reaction Roles"))

    @reactionrole.command(name="add", help="Link an emoji to a role on a message.")
    @commands.bot_has_permissions(manage_roles=True, add_reactions=True)
    async def rr_add(self, ctx, message: discord.Message, emoji: str, *, role: discord.Role):
        if (e := role_error(ctx, role)):
            return await ctx.send(embed=err(e))
        try:
            await self.attach(message, [(parse_emoji(emoji), role)])
        except ValueError as exc:
            return await ctx.send(embed=err(str(exc)))
        await ctx.send(embed=ok(f"{emoji} on [that message]({message.jump_url}) now gives {role.mention}."))

    @reactionrole.command(name="remove", help="Remove one emoji (or all) from a message.")
    async def rr_remove(self, ctx, message: discord.Message, emoji: str = None):
        try:
            parsed = parse_emoji(emoji) if emoji else None
        except ValueError as exc:
            return await ctx.send(embed=err(str(exc)))
        if not await self.detach(ctx.guild, message.id, parsed):
            return await ctx.send(embed=err("No reaction roles found there."))
        await ctx.send(embed=ok("Reaction role removed."))

    @reactionrole.command(name="list", help="Show all reaction roles in this server.")
    async def rr_list(self, ctx):
        data = rr_data(ctx.guild.id)
        if not data:
            return await ctx.send(embed=embed("No reaction roles yet."))
        lines = []
        for mid, entry in data.items():
            pairs = ", ".join(f"{r['emoji']} → <@&{r['role']}>" for r in entry["roles"].values())
            lines.append(f"[Message]({'https://discord.com/channels/%d/%d/%s' % (ctx.guild.id, entry['channel'], mid)}): {pairs}")
        await ctx.send(embed=embed("\n".join(lines)[:4000], title="Reaction Roles"))

    # ---------- listeners ----------
    async def _handle(self, payload: discord.RawReactionActionEvent, add: bool):
        if not payload.guild_id or payload.user_id == self.bot.user.id:
            return
        entry = rr_data(payload.guild_id).get(str(payload.message_id))
        if not entry:
            return
        item = entry["roles"].get(emoji_key(payload.emoji))
        if not item:
            return
        guild = self.bot.get_guild(payload.guild_id)
        role = guild.get_role(item["role"]) if guild else None
        member = (payload.member if add else guild.get_member(payload.user_id)) if guild else None
        if not role or not member or member.bot:
            return
        try:
            if add:
                await member.add_roles(role, reason="Reaction role")
            else:
                await member.remove_roles(role, reason="Reaction role removed")
        except discord.HTTPException:
            pass  # missing permission / role above mine

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self._handle(payload, True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self._handle(payload, False)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        if payload.guild_id and rr_data(payload.guild_id).pop(str(payload.message_id), None) is not None:
            store.save(payload.guild_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        if not payload.guild_id:
            return
        data, changed = rr_data(payload.guild_id), False
        for mid in payload.message_ids:
            changed |= data.pop(str(mid), None) is not None
        if changed:
            store.save(payload.guild_id)


async def setup(bot):
    await bot.add_cog(ReactionRoles(bot))
