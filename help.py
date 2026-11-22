from typing import Optional

import discord
from discord.ext import commands

from utils.helpers import embed, err, BLURPLE


def first_line(cmd) -> str:
    return (cmd.help or "No description").split("\n")[0]


def category_embed(ctx, cog) -> discord.Embed:
    lines = []
    for cmd in sorted(cog.get_commands(), key=lambda c: c.name):
        if cmd.hidden:
            continue
        line = f"`{ctx.clean_prefix}{cmd.name}` — {first_line(cmd)}"
        if isinstance(cmd, commands.Group) and cmd.commands:
            line += "\n  ↳ " + ", ".join(f"`{s.name}`" for s in cmd.commands)
        lines.append(line)
    return discord.Embed(
        title=f"{cog.qualified_name} commands",
        description=("\n".join(lines) or "No commands")[:4000],
        color=BLURPLE,
    )


class HelpSelect(discord.ui.Select):
    def __init__(self, ctx, cogs):
        self.ctx = ctx
        self.cogs = {c.qualified_name: c for c in cogs}
        options = [
            discord.SelectOption(label=c.qualified_name, description=(c.description or "")[:100] or None)
            for c in cogs
        ]
        super().__init__(placeholder="Choose a category…", options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        cog = self.cogs[self.values[0]]
        await interaction.response.edit_message(embed=category_embed(self.ctx, cog))


class HelpView(discord.ui.View):
    def __init__(self, ctx, cogs):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.add_item(HelpSelect(ctx, cogs))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Run the help command yourself to use this menu.", ephemeral=True)
            return False
        return True


class Help(commands.Cog, name="Help"):
    """The help menu."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["h", "commands"], help="Show all commands. Usage: help [command]")
    async def help(self, ctx, *, command: Optional[str] = None):
        if command:
            cmd = self.bot.get_command(command.lower())
            if not cmd or cmd.hidden:
                return await ctx.send(embed=err(f"No command named `{command}`."))
            emb = discord.Embed(
                title=f"{ctx.clean_prefix}{cmd.qualified_name} {cmd.signature}".strip(),
                description=cmd.help or "No description",
                color=BLURPLE)
            if cmd.aliases:
                emb.add_field(name="Aliases", value=", ".join(f"`{a}`" for a in cmd.aliases))
            if isinstance(cmd, commands.Group) and cmd.commands:
                emb.add_field(name="Subcommands", value="\n".join(f"`{s.name}` — {first_line(s)}" for s in cmd.commands), inline=False)
            return await ctx.send(embed=emb)

        cogs = [c for c in self.bot.cogs.values() if any(not x.hidden for x in c.get_commands())]
        cogs.sort(key=lambda c: c.qualified_name)
        lines = [f"**{c.qualified_name}** — {len(c.get_commands())} commands" for c in cogs]
        emb = discord.Embed(
            title=f"{self.bot.user.name} — Help",
            description="\n".join(lines) + f"\n\nPick a category below, or use `{ctx.clean_prefix}help <command>` for details.",
            color=BLURPLE)
        emb.set_thumbnail(url=self.bot.user.display_avatar.url)
        await ctx.send(embed=emb, view=HelpView(ctx, cogs))


async def setup(bot):
    await bot.add_cog(Help(bot))
