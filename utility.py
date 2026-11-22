import time
from typing import Optional

import discord
from discord.ext import commands

from utils.helpers import ok, err, embed, BLURPLE
from utils.store import store

START = time.time()


class Utility(commands.Cog, name="Utility"):
    """Avatar, ping, info, prefix and settings."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["pings", "latency"], help="Show the bot's latency.")
    async def ping(self, ctx):
        await ctx.send(embed=embed(f"🏓 Pong! **{round(self.bot.latency * 1000)} ms**"))

    @commands.command(aliases=["avatar", "pfp"], help="Show someone's avatar. Usage: av [@user]")
    async def av(self, ctx, user: Optional[discord.User] = None):
        user = user or ctx.author
        asset = user.display_avatar
        emb = discord.Embed(title=f"{user.display_name}'s avatar", color=BLURPLE)
        emb.set_image(url=asset.url)
        links = []
        for fmt in ("png", "jpg", "webp"):
            try:
                links.append(f"[{fmt}]({asset.replace(format=fmt, size=1024).url})")
            except ValueError:
                pass
        emb.description = " · ".join(links) or None
        await ctx.send(embed=emb)

    @commands.command(aliases=["ui", "whois"], help="Info about a member. Usage: userinfo [@user]")
    async def userinfo(self, ctx, member: Optional[discord.Member] = None):
        m = member or ctx.author
        roles = [r.mention for r in reversed(m.roles) if not r.is_default()]
        emb = discord.Embed(title=str(m), color=m.color or BLURPLE)
        emb.set_thumbnail(url=m.display_avatar.url)
        emb.add_field(name="ID", value=m.id)
        emb.add_field(name="Created", value=discord.utils.format_dt(m.created_at, "R"))
        emb.add_field(name="Joined", value=discord.utils.format_dt(m.joined_at, "R") if m.joined_at else "?")
        emb.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles[:15]) or "none", inline=False)
        await ctx.send(embed=emb)

    @commands.command(aliases=["si", "server"], help="Info about this server.")
    async def serverinfo(self, ctx):
        g = ctx.guild
        humans = sum(1 for m in g.members if not m.bot)
        emb = discord.Embed(title=g.name, color=BLURPLE)
        if g.icon:
            emb.set_thumbnail(url=g.icon.url)
        emb.add_field(name="Owner", value=g.owner.mention if g.owner else "?")
        emb.add_field(name="Created", value=discord.utils.format_dt(g.created_at, "R"))
        emb.add_field(name="Members", value=f"{g.member_count} ({humans} humans, {g.member_count - humans} bots)")
        emb.add_field(name="Channels", value=len(g.channels))
        emb.add_field(name="Roles", value=len(g.roles))
        emb.add_field(name="Boosts", value=g.premium_subscription_count)
        await ctx.send(embed=emb)

    @commands.command(help="Bot uptime and stats.")
    async def botinfo(self, ctx):
        up = int(time.time() - START)
        d, r = divmod(up, 86400)
        h, r = divmod(r, 3600)
        m, _ = divmod(r, 60)
        await ctx.send(embed=embed(
            f"**Servers:** {len(self.bot.guilds)}\n**Latency:** {round(self.bot.latency * 1000)} ms\n**Uptime:** {d}d {h}h {m}m",
            title=str(self.bot.user)))

    @commands.command(help="View or change the prefix. Usage: prefix [new_prefix]")
    async def prefix(self, ctx, new: Optional[str] = None):
        if new is None:
            return await ctx.send(embed=embed(f"Current prefix: `{ctx.clean_prefix}`"))
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=err("You need Manage Server to change the prefix."))
        if len(new) > 5:
            return await ctx.send(embed=err("Prefix can be at most 5 characters."))
        store.get(ctx.guild.id)["prefix"] = new
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Prefix changed to `{new}`."))

    @commands.command(aliases=["config"], help="Show this server's bot settings.")
    @commands.has_permissions(manage_guild=True)
    async def settings(self, ctx):
        g, c = ctx.guild, store.get(ctx.guild.id)

        def role(rid):
            r = g.get_role(rid or 0)
            return r.mention if r else "off"

        def chan(cid):
            ch = g.get_channel(cid or 0)
            return ch.mention if ch else "off"

        w = c.get("welcome", {})
        ar = c.get("autorole", {})
        am = c.get("automod", {})
        al = am.get("antilink", {})
        bw = am.get("badwords", {})
        t = c.get("tickets", {})
        emb = discord.Embed(title=f"Settings — {g.name}", color=BLURPLE)
        emb.add_field(name="Prefix", value=f"`{c.get('prefix', ';')}`")
        emb.add_field(name="Welcome", value=f"{chan(w.get('channel'))}\nembed: {'on' if w.get('embed') else 'off'} · webhook: {'on' if w.get('webhook_url') else 'off'}")
        emb.add_field(name="Auto roles", value=f"humans: {role(ar.get('humans'))}\nbots: {role(ar.get('bots'))}")
        emb.add_field(name="Verify role", value=role(c.get("verify", {}).get("role")))
        emb.add_field(name="Anti-link", value=f"{'on' if al.get('on') else 'off'} · {al.get('action', 'delete')}")
        emb.add_field(name="Bad words", value=f"{'on' if bw.get('on', True) else 'off'} · {len(bw.get('words', []))} words")
        emb.add_field(name="Tickets", value=f"support: {role(t.get('support_role'))}")
        emb.add_field(name="Triggers", value=str(len(c.get("triggers", {}))))
        emb.add_field(name="Sticky channels", value=str(len(c.get("sticky", {}))))
        lv = c.get("leveling", {})
        emb.add_field(name="Leveling", value=f"{'on' if lv.get('enabled') else 'off'} · {len(lv.get('rewards', {}))} rewards")
        await ctx.send(embed=emb)


async def setup(bot):
    await bot.add_cog(Utility(bot))
