import os
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.store import levels, store

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("Set DISCORD_TOKEN in your .env file or hosting environment.")

DEFAULT_PREFIX = ";"
COGS = [
    "moderation", "roles", "welcome", "verify", "automod", "sticky",
    "trigger", "embeds", "reactionroles", "leveling", "music", "tickets", "utility", "help",
]
OPTIONAL_COGS = {"music"}  # needs extra packages (PyNaCl, yt-dlp) + ffmpeg; the bot still runs without it

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


def get_prefix(bot, message):
    prefix = DEFAULT_PREFIX
    if message.guild:
        prefix = store.get(message.guild.id).get("prefix", DEFAULT_PREFIX)
    return commands.when_mentioned_or(prefix)(bot, message)


intents = discord.Intents.default()
intents.members = True          # enable "Server Members Intent" in the Developer Portal
intents.message_content = True  # enable "Message Content Intent" in the Developer Portal


class AllInOne(commands.Bot):
    dashboard = None

    async def setup_hook(self):
        for name in COGS:
            try:
                await self.load_extension(f"cogs.{name}")
                log.info("Loaded cog: %s", name)
            except Exception:
                if name not in OPTIONAL_COGS:
                    raise
                log.exception("Optional module '%s' did not load (see README: music needs ffmpeg + PyNaCl + yt-dlp)", name)
        try:  # the website is optional: if it fails, the bot still runs
            from web.server import start
            self.dashboard = await start(self)
        except Exception:
            log.exception("Dashboard failed to start (the bot keeps running without it)")

    async def close(self):
        # runs on Ctrl+C / SIGTERM (docker stop, systemctl restart): make sure everything is on disk
        if self.dashboard:
            await self.dashboard.cleanup()
        store.flush_all()
        levels.flush_all()
        await super().close()


bot = AllInOne(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
    case_insensitive=True,
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
)


@bot.check
async def guild_only(ctx):
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    return True


@bot.check
async def inherit_group_checks(ctx):
    """Make a group's permission checks apply to its subcommands too.

    With invoke_without_command=True, discord.py skips the parent group's checks
    when a subcommand runs, which would let anyone run e.g. `;roleall humans @role`.
    """
    parent = getattr(ctx.command, "parent", None)
    while parent is not None:
        for check in parent.checks:
            if not await discord.utils.maybe_coroutine(check, ctx):
                raise commands.CheckFailure()
        parent = parent.parent
    return True


STATUS_TYPES = {"watching": discord.ActivityType.watching, "playing": discord.ActivityType.playing,
                "listening": discord.ActivityType.listening, "competing": discord.ActivityType.competing}


def make_activity():
    """Bot status line, e.g. 'Watching love you ❤️ | 28 watching'. Change with STATUS_TEXT / STATUS_TYPE in .env."""
    text = os.getenv("STATUS_TEXT", "love you ❤️ | 28 watching")[:128]
    kind = STATUS_TYPES.get(os.getenv("STATUS_TYPE", "watching").lower(), discord.ActivityType.watching)
    return discord.Activity(type=kind, name=text)


@bot.event
async def on_ready():
    log.info("Logged in as %s (%s) in %d servers", bot.user, bot.user.id, len(bot.guilds))
    await bot.change_presence(
        status=discord.Status.online,
        activity=make_activity(),
    )


@bot.event
async def on_command_error(ctx, error):
    error = getattr(error, "original", error)
    if isinstance(error, (commands.CommandNotFound, commands.NoPrivateMessage)):
        return
    if isinstance(error, commands.MissingPermissions):
        msg = "You need: " + ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
    elif isinstance(error, commands.BotMissingPermissions):
        msg = "I need: " + ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
    elif isinstance(error, commands.MissingRequiredArgument):
        msg = f"Missing `{error.param.name}`.\nUsage: `{ctx.clean_prefix}{ctx.command.qualified_name} {ctx.command.signature}`"
    elif isinstance(error, commands.CommandOnCooldown):
        msg = f"Slow down! Try again in {error.retry_after:.1f}s."
    elif isinstance(error, (commands.BadArgument, commands.BadUnionArgument)):
        msg = str(error)
    elif isinstance(error, commands.CheckFailure):
        msg = "You can't use this command."
    elif isinstance(error, discord.Forbidden):
        msg = "I don't have permission to do that (check my role position and permissions)."
    else:
        log.exception("Unhandled error in command %s", ctx.command, exc_info=error)
        msg = "Something went wrong."
    try:
        await ctx.send(embed=discord.Embed(description=f"❌ {msg}", color=0xED4245))
    except discord.HTTPException:
        pass


if __name__ == "__main__":
    bot.run(TOKEN, reconnect=True)
