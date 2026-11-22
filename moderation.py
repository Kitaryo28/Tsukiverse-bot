import time
from datetime import timedelta

import discord
from discord.ext import commands

from utils.helpers import ok, err, embed, parse_duration, member_error
from utils.store import store


class Moderation(commands.Cog, name="Moderation"):
    """Ban, kick, mute, warn and channel tools."""

    def __init__(self, bot):
        self.bot = bot

    async def _dm(self, member, text):
        try:
            await member.send(text)
        except discord.HTTPException:
            pass

    @commands.command(help="Ban a member. Usage: ban @user [reason]")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        if (e := member_error(ctx, member)):
            return await ctx.send(embed=err(e))
        await self._dm(member, f"You were banned from **{ctx.guild.name}**. Reason: {reason}")
        await member.ban(reason=f"{ctx.author}: {reason}", delete_message_seconds=0)
        await ctx.send(embed=ok(f"Banned **{member}** — {reason}"))

    @commands.command(help="Unban a user by ID. Usage: unban <user_id> [reason]")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx, user_id: int, *, reason: str = "No reason provided"):
        try:
            await ctx.guild.unban(discord.Object(id=user_id), reason=f"{ctx.author}: {reason}")
        except discord.NotFound:
            return await ctx.send(embed=err("That user isn't banned."))
        await ctx.send(embed=ok(f"Unbanned user `{user_id}`."))

    @commands.command(help="Kick a member. Usage: kick @user [reason]")
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        if (e := member_error(ctx, member)):
            return await ctx.send(embed=err(e))
        await self._dm(member, f"You were kicked from **{ctx.guild.name}**. Reason: {reason}")
        await member.kick(reason=f"{ctx.author}: {reason}")
        await ctx.send(embed=ok(f"Kicked **{member}** — {reason}"))

    @commands.command(aliases=["timeout"], help="Mute (timeout) a member. Usage: mute @user 10m [reason]  (s/m/h/d/w, max 28d)")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(self, ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
        if (e := member_error(ctx, member)):
            return await ctx.send(embed=err(e))
        delta = parse_duration(duration)
        if not delta or delta > timedelta(days=28):
            return await ctx.send(embed=err("Use a duration like `10m`, `2h`, `1d` (max `28d`)."))
        await member.timeout(delta, reason=f"{ctx.author}: {reason}")
        await ctx.send(embed=ok(f"Muted **{member}** for `{duration}` — {reason}"))

    @commands.command(aliases=["untimeout"], help="Unmute a member. Usage: unmute @user")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx, member: discord.Member):
        await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        await ctx.send(embed=ok(f"Unmuted **{member}**."))

    @commands.command(help="Warn a member. Usage: warn @user [reason]")
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        if (e := member_error(ctx, member)):
            return await ctx.send(embed=err(e))
        c = store.get(ctx.guild.id)
        warns = c.setdefault("warns", {}).setdefault(str(member.id), [])
        warns.append({"reason": reason, "mod": ctx.author.id, "time": int(time.time())})
        store.save(ctx.guild.id)
        await self._dm(member, f"You were warned in **{ctx.guild.name}**: {reason}")
        await ctx.send(embed=ok(f"Warned **{member}** (total: {len(warns)}) — {reason}"))

    @commands.command(aliases=["warns"], help="Show a member's warnings. Usage: warnings @user")
    @commands.has_permissions(manage_messages=True)
    async def warnings(self, ctx, member: discord.Member):
        warns = store.get(ctx.guild.id).get("warns", {}).get(str(member.id), [])
        if not warns:
            return await ctx.send(embed=embed(f"{member} has no warnings."))
        lines = [f"`{i}.` <t:{w['time']}:d> — {w['reason']} (by <@{w['mod']}>)" for i, w in enumerate(warns, 1)]
        await ctx.send(embed=embed("\n".join(lines[:20]), title=f"Warnings for {member}"))

    @commands.command(help="Clear all warnings of a member. Usage: clearwarns @user")
    @commands.has_permissions(manage_guild=True)
    async def clearwarns(self, ctx, member: discord.Member):
        c = store.get(ctx.guild.id)
        c.setdefault("warns", {}).pop(str(member.id), None)
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Cleared warnings for **{member}**."))

    @commands.command(aliases=["clear"], help="Delete messages. Usage: purge <1-500>")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(self, ctx, amount: int):
        if not 1 <= amount <= 500:
            return await ctx.send(embed=err("Amount must be between 1 and 500."))
        deleted = await ctx.channel.purge(limit=amount + 1)
        await ctx.send(embed=ok(f"Deleted {max(len(deleted) - 1, 0)} messages."), delete_after=4)

    @commands.command(help="Lock the current channel for @everyone.")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(self, ctx):
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
        await ctx.send(embed=ok("🔒 Channel locked."))

    @commands.command(help="Unlock the current channel for @everyone.")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock(self, ctx):
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None)
        await ctx.send(embed=ok("🔓 Channel unlocked."))

    @commands.command(help="Set channel slowmode in seconds (0 = off). Usage: slowmode <seconds>")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(self, ctx, seconds: int):
        if not 0 <= seconds <= 21600:
            return await ctx.send(embed=err("Seconds must be 0–21600."))
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(embed=ok(f"Slowmode set to {seconds}s."))


async def setup(bot):
    await bot.add_cog(Moderation(bot))
