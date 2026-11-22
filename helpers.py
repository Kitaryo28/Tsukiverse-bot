import re
from datetime import timedelta

import discord

BLURPLE = 0x5865F2
GREEN = 0x57F287
RED = 0xED4245

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def embed(desc=None, *, title=None, color=BLURPLE) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=color)


def ok(text) -> discord.Embed:
    return embed(f"✅ {text}", color=GREEN)


def err(text) -> discord.Embed:
    return embed(f"❌ {text}", color=RED)


def parse_color(text):
    """'#ff0000' / 'ff0000' -> int, or None if invalid."""
    try:
        return int(str(text).strip().lstrip("#"), 16)
    except ValueError:
        return None


def parse_duration(text):
    """'10m', '2h', '1d', '1w' -> timedelta, or None if invalid."""
    m = re.fullmatch(r"(\d+)([smhdw])", str(text).lower().strip())
    if not m:
        return None
    return timedelta(seconds=int(m[1]) * _UNITS[m[2]])


def member_error(ctx, target: discord.Member):
    """Return an error string if ctx.author/bot can't act on target, else None."""
    guild = ctx.guild
    if target == ctx.author:
        return "You can't do that to yourself."
    if target == guild.owner:
        return "You can't do that to the server owner."
    if target == guild.me:
        return "I can't do that to myself."
    if ctx.author != guild.owner and target.top_role >= ctx.author.top_role:
        return "That member's top role is higher than or equal to yours."
    if target.top_role >= guild.me.top_role:
        return "That member's top role is higher than or equal to mine."
    return None


def role_error(ctx, role: discord.Role):
    """Return an error string if the role can't be managed, else None."""
    if role.is_default() or role.managed:
        return "That role can't be assigned manually."
    if ctx.author != ctx.guild.owner and role >= ctx.author.top_role:
        return "That role is higher than or equal to your top role."
    if role >= ctx.guild.me.top_role:
        return "That role is higher than or equal to my top role."
    return None
