import asyncio
import time

import discord
from discord.ext import commands, tasks

from utils.helpers import ok, err, embed, parse_duration, member_error, role_error
from utils.store import store


class Roles(commands.Cog, name="Roles"):
    """Add/remove roles, temp roles, auto roles and role-all."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.temprole_loop.start()

    async def cog_unload(self):
        self.temprole_loop.cancel()

    # ---------- basic ----------
    @commands.command(aliases=["ar"], help="Give a role. Usage: addrole @user <role>")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def addrole(self, ctx, member: discord.Member, *, role: discord.Role):
        if (e := role_error(ctx, role)) or (member != ctx.author and (e := member_error(ctx, member))):
            return await ctx.send(embed=err(e))
        await member.add_roles(role, reason=f"By {ctx.author}")
        await ctx.send(embed=ok(f"Added {role.mention} to **{member}**."))

    @commands.command(aliases=["rr"], help="Remove a role. Usage: removerole @user <role>")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def removerole(self, ctx, member: discord.Member, *, role: discord.Role):
        if (e := role_error(ctx, role)) or (member != ctx.author and (e := member_error(ctx, member))):
            return await ctx.send(embed=err(e))
        await member.remove_roles(role, reason=f"By {ctx.author}")
        await ctx.send(embed=ok(f"Removed {role.mention} from **{member}**."))

    # ---------- temp role ----------
    @commands.command(help="Give a role for a limited time. Usage: temprole @user @role 1h")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def temprole(self, ctx, member: discord.Member, role: discord.Role, duration: str):
        if (e := role_error(ctx, role)) or (member != ctx.author and (e := member_error(ctx, member))):
            return await ctx.send(embed=err(e))
        delta = parse_duration(duration)
        if not delta:
            return await ctx.send(embed=err("Use a duration like `30m`, `2h`, `7d`."))
        expires = int(time.time() + delta.total_seconds())
        await member.add_roles(role, reason=f"Temp role by {ctx.author}")
        c = store.get(ctx.guild.id)
        lst = c.setdefault("temproles", [])
        lst[:] = [x for x in lst if not (x["user"] == member.id and x["role"] == role.id)]
        lst.append({"user": member.id, "role": role.id, "expires": expires})
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Gave {role.mention} to **{member}** until <t:{expires}:f> (<t:{expires}:R>)."))

    @tasks.loop(seconds=30)
    async def temprole_loop(self):
        now = time.time()
        for guild in self.bot.guilds:
            c = store.get(guild.id)
            entries = c.get("temproles", [])
            if not entries:
                continue
            keep = []
            for e in entries:
                if e["expires"] > now:
                    keep.append(e)
                    continue
                member, role = guild.get_member(e["user"]), guild.get_role(e["role"])
                if member and role:
                    try:
                        await member.remove_roles(role, reason="Temp role expired")
                    except discord.HTTPException:
                        pass
            if len(keep) != len(entries):
                c["temproles"] = keep
                store.save(guild.id)

    @temprole_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ---------- auto role ----------
    @commands.group(invoke_without_command=True, help="Auto roles on join. Usage: autorole humans @role | bots @role | remove humans|bots")
    @commands.has_permissions(manage_roles=True)
    async def autorole(self, ctx):
        c = store.get(ctx.guild.id).get("autorole", {})
        h, b = ctx.guild.get_role(c.get("humans", 0)), ctx.guild.get_role(c.get("bots", 0))
        await ctx.send(embed=embed(
            f"**Humans:** {h.mention if h else 'not set'}\n**Bots:** {b.mention if b else 'not set'}\n\n"
            f"`{ctx.clean_prefix}autorole humans @role`\n`{ctx.clean_prefix}autorole bots @role`\n"
            f"`{ctx.clean_prefix}autorole remove humans|bots`", title="Auto Roles"))

    @autorole.command(name="humans", help="Role given to every human who joins.")
    async def ar_humans(self, ctx, role: discord.Role):
        if (e := role_error(ctx, role)):
            return await ctx.send(embed=err(e))
        store.get(ctx.guild.id).setdefault("autorole", {})["humans"] = role.id
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"New humans will get {role.mention}."))

    @autorole.command(name="bots", help="Role given to every bot that joins.")
    async def ar_bots(self, ctx, role: discord.Role):
        if (e := role_error(ctx, role)):
            return await ctx.send(embed=err(e))
        store.get(ctx.guild.id).setdefault("autorole", {})["bots"] = role.id
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"New bots will get {role.mention}."))

    @autorole.command(name="remove", help="Disable auto role. Usage: autorole remove humans|bots")
    async def ar_remove(self, ctx, target: str):
        target = target.lower()
        if target not in ("humans", "bots"):
            return await ctx.send(embed=err("Choose `humans` or `bots`."))
        store.get(ctx.guild.id).setdefault("autorole", {}).pop(target, None)
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Auto role for {target} removed."))

    @commands.Cog.listener()
    async def on_member_join(self, member):
        c = store.get(member.guild.id).get("autorole", {})
        role = member.guild.get_role(c.get("bots" if member.bot else "humans", 0))
        if role:
            try:
                await member.add_roles(role, reason="Auto role")
            except discord.HTTPException:
                pass

    # ---------- role all ----------
    async def _bulk(self, ctx, role, predicate, add=True):
        if (e := role_error(ctx, role)):
            return await ctx.send(embed=err(e))
        targets = [m for m in ctx.guild.members if predicate(m) and ((role not in m.roles) if add else (role in m.roles))]
        if not targets:
            return await ctx.send(embed=err("No members to update."))
        msg = await ctx.send(embed=embed(f"⏳ {'Adding' if add else 'Removing'} {role.mention} for {len(targets)} members…"))
        done = 0
        for m in targets:
            try:
                if add:
                    await m.add_roles(role, reason=f"Role all by {ctx.author}")
                else:
                    await m.remove_roles(role, reason=f"Role all removal by {ctx.author}")
                done += 1
            except discord.HTTPException:
                pass
            await asyncio.sleep(0.6)
        await msg.edit(embed=ok(f"Done — updated {done}/{len(targets)} members with {role.mention}."))

    @commands.group(invoke_without_command=True, help="Give a role to everyone. Usage: roleall @role  (or: roleall humans @role / roleall bots @role)")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.cooldown(1, 30, commands.BucketType.guild)
    async def roleall(self, ctx, *, role: discord.Role):
        await self._bulk(ctx, role, lambda m: True)

    @roleall.command(name="humans", help="Give a role to all humans.")
    async def ra_humans(self, ctx, *, role: discord.Role):
        await self._bulk(ctx, role, lambda m: not m.bot)

    @roleall.command(name="bots", help="Give a role to all bots.")
    async def ra_bots(self, ctx, *, role: discord.Role):
        await self._bulk(ctx, role, lambda m: m.bot)

    @commands.group(invoke_without_command=True, help="Remove a role from everyone. Usage: removeroleall @role  (or humans/bots)")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.cooldown(1, 30, commands.BucketType.guild)
    async def removeroleall(self, ctx, *, role: discord.Role):
        await self._bulk(ctx, role, lambda m: True, add=False)

    @removeroleall.command(name="humans", help="Remove a role from all humans.")
    async def rra_humans(self, ctx, *, role: discord.Role):
        await self._bulk(ctx, role, lambda m: not m.bot, add=False)

    @removeroleall.command(name="bots", help="Remove a role from all bots.")
    async def rra_bots(self, ctx, *, role: discord.Role):
        await self._bulk(ctx, role, lambda m: m.bot, add=False)


async def setup(bot):
    await bot.add_cog(Roles(bot))
