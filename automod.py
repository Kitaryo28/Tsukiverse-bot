import copy
import re
from datetime import timedelta

import discord
from discord.ext import commands

from utils.helpers import ok, err, embed, parse_duration
from utils.linkfilter import GIF_DOMAINS, domain_of, find_blocked_link  # noqa: F401  (domain_of is used by the website too)
from utils.store import store

ACTIONS = ("delete", "warn", "timeout", "kick", "ban")   # the message is always deleted first
MAX_TIMEOUT_MINUTES = 40320                              # Discord's limit: 28 days

# Anti-link: by default it deletes the link instantly AND times the member out. GIF links are allowed.
ANTILINK_DEFAULTS = {"on": False, "action": "timeout", "timeout_minutes": 10, "allow_gifs": True, "whitelist": []}
BADWORD_DEFAULTS = {"on": True, "action": "delete", "timeout_minutes": 10, "words": []}


def am(gid) -> dict:
    c = store.get(gid).setdefault("automod", {})
    for key, defaults in (("antilink", ANTILINK_DEFAULTS), ("badwords", BADWORD_DEFAULTS)):
        section = c.setdefault(key, {})
        for k, v in defaults.items():
            section.setdefault(k, copy.deepcopy(v))
    c.setdefault("bypass_roles", [])
    c.setdefault("bypass_channels", [])
    return c


def fmt_minutes(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


class AutoMod(commands.Cog, name="AutoMod"):
    """Anti-link (with auto ban), bad words filter, bypass lists."""

    def __init__(self, bot):
        self.bot = bot

    # ---------- enforcement ----------
    def _exempt(self, message, c) -> bool:
        m = message.author
        if m.guild_permissions.manage_messages or m.guild_permissions.administrator:
            return True
        if message.channel.id in c["bypass_channels"]:
            return True
        return any(r.id in c["bypass_roles"] for r in m.roles)

    async def _punish(self, message, action, reason, minutes=10):
        member = message.author
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        label = "Message removed"
        try:
            if action == "warn":
                w = store.get(message.guild.id).setdefault("warns", {}).setdefault(str(member.id), [])
                w.append({"reason": reason, "mod": self.bot.user.id, "time": int(message.created_at.timestamp())})
                store.save(message.guild.id)
                label = f"Warned (total {len(w)})"
            elif action == "timeout":
                await member.timeout(timedelta(minutes=minutes), reason=reason)
                label = f"Timed out for {fmt_minutes(minutes)}"
            elif action == "kick":
                await member.kick(reason=reason)
                label = "Kicked"
            elif action == "ban":
                await member.ban(reason=reason, delete_message_seconds=3600)
                label = "Banned"
        except discord.HTTPException:
            label = "Message removed (I lack permission to punish this member)"
        try:
            await message.channel.send(embed=embed(f"🛡️ {member.mention} — {reason}. **{label}.**"), delete_after=6)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # stop people bypassing filters by editing a clean message afterwards
        if before.content != after.content:
            await self.on_message(after)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return
        c = am(message.guild.id)
        if self._exempt(message, c):
            return

        al = c["antilink"]
        if al["on"] and find_blocked_link(message.content, al["whitelist"], al["allow_gifs"]):
            return await self._punish(message, al["action"], "Links are not allowed", al["timeout_minutes"])

        bw = c["badwords"]
        if bw["on"] and bw["words"]:
            text = message.content.lower()
            for word in bw["words"]:
                if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text):
                    return await self._punish(message, bw["action"], "Watch your language", bw["timeout_minutes"])

    # ---------- anti-link config ----------
    @commands.group(invoke_without_command=True, aliases=["autolink"], help="Anti-link: on|off, timeout <time>, gifs on|off, action, whitelist, test")
    @commands.has_permissions(manage_guild=True)
    async def antilink(self, ctx):
        al = am(ctx.guild.id)["antilink"]
        wl = ", ".join(al["whitelist"]) or "none"
        p = ctx.clean_prefix
        punishment = f"timeout for **{fmt_minutes(al['timeout_minutes'])}**" if al["action"] == "timeout" else f"**{al['action']}**"
        await ctx.send(embed=embed(
            f"**Status:** {'✅ ON' if al['on'] else '❌ OFF'}\n"
            f"**When someone posts a link:** message deleted instantly + {punishment}\n"
            f"**GIFs allowed:** {'yes' if al['allow_gifs'] else 'no'} (Tenor, Giphy, Klipy, .gif links)\n"
            f"**Allowed domains:** {wl}\n"
            f"Members with Manage Messages, bypass roles and bypass channels are never touched.\n\n"
            f"`{p}antilink on` · `{p}antilink off`\n"
            f"`{p}antilink timeout <10m|1h|1d>` · `{p}antilink action <delete|warn|timeout|kick|ban>`\n"
            f"`{p}antilink gifs on|off` · `{p}antilink whitelist add|remove <domain>`\n"
            f"`{p}antilink test <text>` — check whether a message would be blocked", title="Anti-Link"))

    @antilink.command(name="timeout", help="How long to time the member out, e.g. 10m, 1h, 1d (also sets the action to timeout).")
    async def al_timeout(self, ctx, duration: str):
        delta = parse_duration(duration)
        minutes = int(delta.total_seconds() // 60) if delta else 0
        if not 1 <= minutes <= MAX_TIMEOUT_MINUTES:
            return await ctx.send(embed=err("Use a time like `10m`, `1h` or `1d` (1 minute to 28 days)."))
        al = am(ctx.guild.id)["antilink"]
        al["timeout_minutes"], al["action"] = minutes, "timeout"
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Anyone who posts a link is now deleted and timed out for **{fmt_minutes(minutes)}**."))

    @antilink.command(name="gifs", help="Allow GIF links (on) or block them like other links (off).")
    async def al_gifs(self, ctx, mode: str):
        if mode.lower() not in ("on", "off"):
            return await ctx.send(embed=err("Use `on` or `off`."))
        am(ctx.guild.id)["antilink"]["allow_gifs"] = mode.lower() == "on"
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"GIF links are now **{'allowed' if mode.lower() == 'on' else 'blocked'}**."))

    @antilink.command(name="test", help="See whether a message would be blocked.")
    async def al_test(self, ctx, *, text: str):
        al = am(ctx.guild.id)["antilink"]
        hit = find_blocked_link(text, al["whitelist"], al["allow_gifs"])
        await ctx.send(embed=err(f"Would be **blocked** (link: `{hit[:100]}`).") if hit else ok("Would be **allowed**."),
                       allowed_mentions=discord.AllowedMentions.none())

    @antilink.command(name="on", help="Enable anti-link.")
    async def al_on(self, ctx):
        al = am(ctx.guild.id)["antilink"]
        al["on"] = True
        store.save(ctx.guild.id)
        what = f"timed out for **{fmt_minutes(al['timeout_minutes'])}**" if al["action"] == "timeout" else f"punished (**{al['action']}**)"
        await ctx.send(embed=ok(f"Anti-link is **ON**. Links are deleted instantly and the sender is {what}. "
                                f"GIFs are {'allowed' if al['allow_gifs'] else 'blocked'}."))

    @antilink.command(name="off", help="Disable anti-link.")
    async def al_off(self, ctx):
        am(ctx.guild.id)["antilink"]["on"] = False
        store.save(ctx.guild.id)
        await ctx.send(embed=ok("Anti-link disabled."))

    @antilink.command(name="action", help="What happens when someone posts a link.")
    async def al_action(self, ctx, action: str):
        action = action.lower()
        if action not in ACTIONS:
            return await ctx.send(embed=err("Choose: " + ", ".join(ACTIONS)))
        am(ctx.guild.id)["antilink"]["action"] = action
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Anti-link action set to **{action}**."))

    @antilink.group(name="whitelist", invoke_without_command=True, help="Allowed domains.")
    async def al_wl(self, ctx):
        wl = am(ctx.guild.id)["antilink"]["whitelist"]
        await ctx.send(embed=embed("\n".join(f"• {d}" for d in wl) or "No whitelisted domains.", title="Whitelist"))

    @al_wl.command(name="add", help="Allow a domain, e.g. youtube.com")
    async def wl_add(self, ctx, domain: str):
        d = domain_of(domain)
        wl = am(ctx.guild.id)["antilink"]["whitelist"]
        if d not in wl:
            wl.append(d)
            store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Whitelisted `{d}`."))

    @al_wl.command(name="remove", help="Remove a domain from the whitelist.")
    async def wl_remove(self, ctx, domain: str):
        d = domain_of(domain)
        wl = am(ctx.guild.id)["antilink"]["whitelist"]
        if d in wl:
            wl.remove(d)
            store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Removed `{d}`."))

    # ---------- bad words config ----------
    @commands.group(invoke_without_command=True, aliases=["badwords", "bw"], help="Bad words filter: badword add|remove|list|on|off|action")
    @commands.has_permissions(manage_guild=True)
    async def badword(self, ctx):
        bw = am(ctx.guild.id)["badwords"]
        p = ctx.clean_prefix
        await ctx.send(embed=embed(
            f"**Status:** {'ON' if bw['on'] else 'OFF'}\n**Action:** {bw['action']}\n**Words:** {len(bw['words'])}\n\n"
            f"`{p}badword add <word>` · `{p}badword remove <word>` · `{p}badword list`\n"
            f"`{p}badword on|off` · `{p}badword action <delete|warn|timeout|kick|ban>`", title="Bad Words"))

    @badword.command(name="add", help="Add a banned word/phrase.")
    async def bw_add(self, ctx, *, word: str):
        word = word.lower().strip()
        bw = am(ctx.guild.id)["badwords"]
        if word not in bw["words"]:
            bw["words"].append(word)
            store.save(ctx.guild.id)
        try:
            await ctx.message.delete()  # hide the word
        except discord.HTTPException:
            pass
        await ctx.send(embed=ok("Word added."), delete_after=4)

    @badword.command(name="remove", help="Remove a banned word.")
    async def bw_remove(self, ctx, *, word: str):
        word = word.lower().strip()
        bw = am(ctx.guild.id)["badwords"]
        if word in bw["words"]:
            bw["words"].remove(word)
            store.save(ctx.guild.id)
        await ctx.send(embed=ok("Word removed."))

    @badword.command(name="list", help="Show banned words (sent privately by DM).")
    async def bw_list(self, ctx):
        words = am(ctx.guild.id)["badwords"]["words"]
        try:
            await ctx.author.send("Banned words: " + (", ".join(words) or "none"))
            await ctx.send(embed=ok("Sent you a DM."))
        except discord.HTTPException:
            await ctx.send(embed=err("I couldn't DM you. Enable DMs from server members."))

    @badword.command(name="on", help="Enable the filter.")
    async def bw_on(self, ctx):
        am(ctx.guild.id)["badwords"]["on"] = True
        store.save(ctx.guild.id)
        await ctx.send(embed=ok("Bad words filter enabled."))

    @badword.command(name="off", help="Disable the filter.")
    async def bw_off(self, ctx):
        am(ctx.guild.id)["badwords"]["on"] = False
        store.save(ctx.guild.id)
        await ctx.send(embed=ok("Bad words filter disabled."))

    @badword.command(name="action", help="Action on bad word.")
    async def bw_action(self, ctx, action: str):
        action = action.lower()
        if action not in ACTIONS:
            return await ctx.send(embed=err("Choose: " + ", ".join(ACTIONS)))
        am(ctx.guild.id)["badwords"]["action"] = action
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"Bad word action set to **{action}**."))

    # ---------- bypass ----------
    @commands.group(invoke_without_command=True, help="Exempt roles/channels from automod: automod bypassrole @role | bypasschannel #channel")
    @commands.has_permissions(manage_guild=True)
    async def automod(self, ctx):
        c = am(ctx.guild.id)
        roles = " ".join(f"<@&{r}>" for r in c["bypass_roles"]) or "none"
        chans = " ".join(f"<#{ch}>" for ch in c["bypass_channels"]) or "none"
        await ctx.send(embed=embed(f"**Bypass roles:** {roles}\n**Bypass channels:** {chans}\n\n"
                                   f"Members with Manage Messages are always exempt.", title="AutoMod Bypass"))

    @automod.command(name="bypassrole", help="Toggle a bypass role.")
    async def am_role(self, ctx, role: discord.Role):
        lst = am(ctx.guild.id)["bypass_roles"]
        (lst.remove if role.id in lst else lst.append)(role.id)
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"{role.mention} is now {'bypassing' if role.id in lst else 'no longer bypassing'} automod."))

    @automod.command(name="bypasschannel", help="Toggle a bypass channel.")
    async def am_chan(self, ctx, channel: discord.TextChannel):
        lst = am(ctx.guild.id)["bypass_channels"]
        (lst.remove if channel.id in lst else lst.append)(channel.id)
        store.save(ctx.guild.id)
        await ctx.send(embed=ok(f"{channel.mention} is now {'bypassing' if channel.id in lst else 'no longer bypassing'} automod."))


async def setup(bot):
    await bot.add_cog(AutoMod(bot))
