import discord
from discord.ext import commands

from utils.helpers import ok, err, embed, parse_color, BLURPLE
from utils.store import store

DEFAULT_MSG = "Welcome {user} to **{server}**! You are member #{count}."


def fmt(text, m: discord.Member):
    return (
        (text or "")
        .replace("{user}", m.mention)
        .replace("{username}", m.name)
        .replace("{server}", m.guild.name)
        .replace("{count}", str(m.guild.member_count))
    )


class Welcome(commands.Cog, name="Welcome"):
    """Welcome messages: plain, custom embed, or via webhook."""

    def __init__(self, bot):
        self.bot = bot

    def _cfg(self, gid) -> dict:
        return store.get(gid).setdefault("welcome", {})

    def _save(self, gid):
        store.save(gid)

    async def send_welcome(self, member: discord.Member):
        w = store.get(member.guild.id).get("welcome") or {}
        if not w.get("channel"):
            return
        channel = member.guild.get_channel(w["channel"])
        if not channel:
            return
        text = fmt(w.get("message", DEFAULT_MSG), member)
        kwargs = {}
        if w.get("embed"):
            emb = discord.Embed(
                title=fmt(w.get("title"), member) or None,
                description=text,
                color=w.get("color", BLURPLE),
            )
            emb.set_thumbnail(url=member.display_avatar.url)
            if w.get("image"):
                emb.set_image(url=w["image"])
            if w.get("footer"):
                emb.set_footer(text=fmt(w["footer"], member))
            kwargs["embed"] = emb
            kwargs["content"] = member.mention
        else:
            kwargs["content"] = text

        if w.get("webhook_url"):
            try:
                hook = discord.Webhook.from_url(w["webhook_url"], client=self.bot)
                await hook.send(
                    username=w.get("webhook_name") or self.bot.user.name,
                    avatar_url=w.get("webhook_avatar") or self.bot.user.display_avatar.url,
                    allowed_mentions=discord.AllowedMentions(users=True),
                    **kwargs,
                )
                return
            except (discord.NotFound, discord.HTTPException, ValueError):
                pass  # fall back to a normal message
        try:
            await channel.send(allowed_mentions=discord.AllowedMentions(users=True), **kwargs)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.send_welcome(member)

    @commands.group(invoke_without_command=True, help="Configure welcome messages.")
    @commands.has_permissions(manage_guild=True)
    async def welcome(self, ctx):
        p = ctx.clean_prefix
        await ctx.send(embed=embed(
            f"`{p}welcome channel #channel`\n"
            f"`{p}welcome message <text>` — placeholders: `{{user}} {{username}} {{server}} {{count}}`\n"
            f"`{p}welcome embed on|off`\n"
            f"`{p}welcome title <text>` · `{p}welcome color <#hex>`\n"
            f"`{p}welcome image <url|off>` · `{p}welcome footer <text|off>`\n"
            f"`{p}welcome webhook on|off`\n"
            f"`{p}welcome webhookname <name>` · `{p}welcome webhookavatar <url>`\n"
            f"`{p}welcome test` · `{p}welcome disable`",
            title="Welcome Setup"))

    @welcome.command(name="channel", help="Set the welcome channel.")
    async def w_channel(self, ctx, channel: discord.TextChannel):
        self._cfg(ctx.guild.id)["channel"] = channel.id
        self._save(ctx.guild.id)
        await ctx.send(embed=ok(f"Welcome channel set to {channel.mention}."))

    @welcome.command(name="message", help="Set the welcome text.")
    async def w_message(self, ctx, *, text: str):
        self._cfg(ctx.guild.id)["message"] = text
        self._save(ctx.guild.id)
        await ctx.send(embed=ok("Welcome message updated. Use `welcome test` to preview."))

    @welcome.command(name="embed", help="Send the welcome as an embed. Usage: welcome embed on|off")
    async def w_embed(self, ctx, state: str):
        self._cfg(ctx.guild.id)["embed"] = state.lower() in ("on", "true", "yes", "enable")
        self._save(ctx.guild.id)
        await ctx.send(embed=ok(f"Embed welcome {'enabled' if self._cfg(ctx.guild.id)['embed'] else 'disabled'}."))

    @welcome.command(name="title", help="Embed title.")
    async def w_title(self, ctx, *, text: str):
        self._cfg(ctx.guild.id)["title"] = text
        self._save(ctx.guild.id)
        await ctx.send(embed=ok("Embed title updated."))

    @welcome.command(name="color", aliases=["colour"], help="Embed color, e.g. #5865F2")
    async def w_color(self, ctx, color: str):
        value = parse_color(color)
        if value is None:
            return await ctx.send(embed=err("Give a hex color like `#5865F2`."))
        self._cfg(ctx.guild.id)["color"] = value
        self._save(ctx.guild.id)
        await ctx.send(embed=ok("Embed color updated."))

    @welcome.command(name="image", help="Embed image URL, or 'off'.")
    async def w_image(self, ctx, url: str):
        c = self._cfg(ctx.guild.id)
        if url.lower() == "off":
            c.pop("image", None)
        else:
            c["image"] = url
        self._save(ctx.guild.id)
        await ctx.send(embed=ok("Embed image updated."))

    @welcome.command(name="footer", help="Embed footer text, or 'off'.")
    async def w_footer(self, ctx, *, text: str):
        c = self._cfg(ctx.guild.id)
        if text.lower() == "off":
            c.pop("footer", None)
        else:
            c["footer"] = text
        self._save(ctx.guild.id)
        await ctx.send(embed=ok("Embed footer updated."))

    @welcome.command(name="webhook", help="Send welcomes through a webhook. Usage: welcome webhook on|off")
    @commands.bot_has_permissions(manage_webhooks=True)
    async def w_webhook(self, ctx, state: str):
        c = self._cfg(ctx.guild.id)
        if state.lower() in ("off", "false", "no", "disable"):
            c.pop("webhook_url", None)
            self._save(ctx.guild.id)
            return await ctx.send(embed=ok("Webhook welcome disabled."))
        channel = ctx.guild.get_channel(c.get("channel", 0))
        if not channel:
            return await ctx.send(embed=err("Set a channel first: `welcome channel #channel`."))
        hook = await channel.create_webhook(name=c.get("webhook_name") or "Welcome", reason="Welcome webhook")
        c["webhook_url"] = hook.url
        self._save(ctx.guild.id)
        await ctx.send(embed=ok(f"Webhook created in {channel.mention}. Customize with `welcome webhookname` / `welcome webhookavatar`."))

    @welcome.command(name="webhookname", help="Name shown on the webhook.")
    async def w_whname(self, ctx, *, name: str):
        self._cfg(ctx.guild.id)["webhook_name"] = name[:80]
        self._save(ctx.guild.id)
        await ctx.send(embed=ok("Webhook name updated."))

    @welcome.command(name="webhookavatar", help="Avatar URL shown on the webhook.")
    async def w_whavatar(self, ctx, url: str):
        self._cfg(ctx.guild.id)["webhook_avatar"] = url
        self._save(ctx.guild.id)
        await ctx.send(embed=ok("Webhook avatar updated."))

    @welcome.command(name="test", help="Preview the welcome with yourself.")
    async def w_test(self, ctx):
        if not self._cfg(ctx.guild.id).get("channel"):
            return await ctx.send(embed=err("Set a channel first: `welcome channel #channel`."))
        await self.send_welcome(ctx.author)
        await ctx.message.add_reaction("✅")

    @welcome.command(name="disable", aliases=["off"], help="Turn welcome messages off.")
    async def w_disable(self, ctx):
        store.get(ctx.guild.id).pop("welcome", None)
        self._save(ctx.guild.id)
        await ctx.send(embed=ok("Welcome messages disabled and settings cleared."))


async def setup(bot):
    await bot.add_cog(Welcome(bot))
