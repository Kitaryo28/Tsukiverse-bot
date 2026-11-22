import asyncio

import discord
from discord.ext import commands

from utils.helpers import ok, err, BLURPLE
from utils.store import store


class Sticky(commands.Cog, name="Sticky"):
    """Sticky messages that stay at the bottom of a channel."""

    def __init__(self, bot):
        self.bot = bot
        self.pending = set()

    def _all(self, gid) -> dict:
        return store.get(gid).setdefault("sticky", {})

    @commands.command(help="Set a sticky message in this channel. Usage: sticky <text>")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def sticky(self, ctx, *, text: str):
        data = self._all(ctx.guild.id)
        old = data.get(str(ctx.channel.id))
        if old and old.get("last"):
            try:
                await (await ctx.channel.fetch_message(old["last"])).delete()
            except discord.HTTPException:
                pass
        data[str(ctx.channel.id)] = {"text": text, "last": None}
        await self._repost(ctx.channel)
        await ctx.send(embed=ok("Sticky message set for this channel."), delete_after=4)

    @commands.command(help="Remove this channel's sticky message.")
    @commands.has_permissions(manage_messages=True)
    async def unsticky(self, ctx):
        data = self._all(ctx.guild.id)
        entry = data.pop(str(ctx.channel.id), None)
        if not entry:
            return await ctx.send(embed=err("No sticky message in this channel."))
        if entry.get("last"):
            try:
                await (await ctx.channel.fetch_message(entry["last"])).delete()
            except discord.HTTPException:
                pass
        store.save(ctx.guild.id)
        await ctx.send(embed=ok("Sticky message removed."))

    async def _repost(self, channel):
        data = self._all(channel.guild.id)
        entry = data.get(str(channel.id))
        if not entry:
            return
        if entry.get("last"):
            try:
                await (await channel.fetch_message(entry["last"])).delete()
            except discord.HTTPException:
                pass
        emb = discord.Embed(description=entry["text"], color=BLURPLE)
        emb.set_footer(text="📌 Sticky message")
        try:
            msg = await channel.send(embed=emb)
        except discord.HTTPException:
            return
        entry["last"] = msg.id
        store.save(channel.guild.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.id == self.bot.user.id:
            return
        if str(message.channel.id) not in self._all(message.guild.id):
            return
        if message.channel.id in self.pending:
            return
        self.pending.add(message.channel.id)
        try:
            await asyncio.sleep(2)  # wait for the burst to settle
            await self._repost(message.channel)
        finally:
            self.pending.discard(message.channel.id)


async def setup(bot):
    await bot.add_cog(Sticky(bot))
