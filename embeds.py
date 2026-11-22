from typing import Optional

import discord
from discord.ext import commands

from utils.helpers import ok, err, embed, parse_color, BLURPLE


class Embeds(commands.Cog, name="Embeds"):
    """Send plain messages and custom embeds as the bot."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(help="Send a message as the bot. Usage: say [#channel] <text>")
    @commands.has_permissions(manage_messages=True)
    async def say(self, ctx, channel: Optional[discord.TextChannel] = None, *, text: str):
        target = channel or ctx.channel
        await target.send(text, allowed_mentions=discord.AllowedMentions.none())
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command(aliases=["esay"], help=(
        "Send a custom embed. Usage: embed [#channel] title | description | #color | image_url | footer\n"
        "Leave a part empty to skip it, e.g.  embed Rules | Be nice | #ff0000"))
    @commands.has_permissions(manage_messages=True)
    async def embed(self, ctx, channel: Optional[discord.TextChannel] = None, *, spec: str):
        parts = [p.strip() for p in spec.split("|")]
        parts += [""] * (5 - len(parts))
        title, desc, color, image, footer = parts[:5]
        if not (title or desc):
            return await ctx.send(embed=err("Give at least a title or a description."))
        value = parse_color(color) if color else BLURPLE
        if value is None:
            return await ctx.send(embed=err("Invalid color. Use hex like `#5865F2`."))
        emb = discord.Embed(title=title or None, description=desc.replace("\\n", "\n") or None, color=value)
        if image:
            emb.set_image(url=image)
        if footer:
            emb.set_footer(text=footer)
        target = channel or ctx.channel
        try:
            await target.send(embed=emb)
        except discord.HTTPException:
            return await ctx.send(embed=err("Couldn't send that embed (check the image URL and my permissions)."))
        if target != ctx.channel:
            await ctx.send(embed=ok(f"Embed sent to {target.mention}."))
        else:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

    @commands.command(help="Edit an embed the bot sent. Usage: editembed <message_id> title | description | #color | image | footer")
    @commands.has_permissions(manage_messages=True)
    async def editembed(self, ctx, message_id: int, *, spec: str):
        try:
            msg = await ctx.channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send(embed=err("Message not found in this channel."))
        if msg.author.id != self.bot.user.id:
            return await ctx.send(embed=err("I can only edit my own messages."))
        parts = [p.strip() for p in spec.split("|")]
        parts += [""] * (5 - len(parts))
        title, desc, color, image, footer = parts[:5]
        value = parse_color(color) if color else BLURPLE
        if value is None:
            return await ctx.send(embed=err("Invalid color."))
        emb = discord.Embed(title=title or None, description=desc.replace("\\n", "\n") or None, color=value)
        if image:
            emb.set_image(url=image)
        if footer:
            emb.set_footer(text=footer)
        await msg.edit(embed=emb)
        await ctx.send(embed=ok("Embed updated."), delete_after=4)


async def setup(bot):
    await bot.add_cog(Embeds(bot))
