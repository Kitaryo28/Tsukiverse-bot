import re

import discord
from discord.ext import commands

from utils.helpers import ok, err, embed
from utils.store import store


class Trigger(commands.Cog, name="Triggers"):
    """Auto-replies when a word or phrase is said."""

    def __init__(self, bot):
        self.bot = bot

    def _all(self, gid) -> dict:
        return store.get(gid).setdefault("triggers", {})

    @commands.group(invoke_without_command=True, help="Auto replies: trigger add \"word\" <reply> | remove <word> | list")
    @commands.has_permissions(manage_guild=True)
    async def trigger(self, ctx):
        p = ctx.clean_prefix
        await ctx.send(embed=embed(
            f"`{p}trigger add \"hello bot\" Hi {{user}}!`\n`{p}trigger remove \"hello bot\"`\n`{p}trigger list`\n\n"
            f"Placeholders: `{{user}} {{username}} {{server}}`. Use quotes for multi-word triggers.", title="Triggers"))

    @trigger.command(name="add", help="Add a trigger. Usage: trigger add \"phrase\" <reply>")
    async def t_add(self, ctx, phrase: str, *, reply: str):
        self._all(ctx.guild.id)[phrase.lower()] = reply
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Trigger `{phrase}` added."))

    @trigger.command(name="remove", aliases=["delete"], help="Remove a trigger.")
    async def t_remove(self, ctx, *, phrase: str):
        if self._all(ctx.guild.id).pop(phrase.lower().strip('"'), None) is None:
            return await ctx.send(embed=err("No such trigger."))
        store.save(ctx.guild.id)
        await ctx.send(embed=ok("Trigger removed."))

    @trigger.command(name="list", help="List triggers.")
    async def t_list(self, ctx):
        data = self._all(ctx.guild.id)
        if not data:
            return await ctx.send(embed=embed("No triggers yet."))
        lines = [f"• `{k}` → {v[:60]}" for k, v in data.items()]
        await ctx.send(embed=embed("\n".join(lines)[:4000], title=f"Triggers ({len(data)})"))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        triggers = store.get(message.guild.id).get("triggers")
        if not triggers:
            return
        prefix = (await self.bot.get_prefix(message))
        prefixes = (prefix,) if isinstance(prefix, str) else tuple(prefix)
        if message.content.startswith(prefixes):
            return
        text = message.content.lower()
        for phrase, reply in triggers.items():
            if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text):  # whole word/phrase only
                out = (reply.replace("{user}", message.author.mention)
                            .replace("{username}", message.author.name)
                            .replace("{server}", message.guild.name))
                try:
                    await message.reply(out, mention_author=False)
                except discord.HTTPException:
                    pass
                break


async def setup(bot):
    await bot.add_cog(Trigger(bot))
