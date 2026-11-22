"""Website dashboard, served from inside the bot process (aiohttp comes with discord.py).

Login is "Login with Discord" (OAuth2, scope `identify` only). A user can open a server's
dashboard only if they own it or have Manage Server there (checked live against the bot's own
view of the server, so removing someone's permission removes their access immediately).

All IDs are sent to the browser as strings (Discord IDs are bigger than JavaScript can hold).
Settings are written through utils.store, i.e. straight to disk.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import aiohttp
import discord
from aiohttp import web

from cogs.automod import ACTIONS, am, domain_of
from cogs.leveling import ANNOUNCE_MODES, lv_cfg
from cogs.tickets import tcfg as ticket_cfg
from utils.ticketcore import (DEFAULT_WELCOME, MAX_BUTTONS, MAX_PANELS, PLACEHOLDER_HELP, STYLE_NAMES, clean_channel_part,
                              next_id, normalize_emoji)
from cogs.reactionroles import emoji_key, parse_emoji, rr_data
from utils.helpers import BLURPLE, parse_color
from utils.store import DATA, store

log = logging.getLogger("dashboard")
STATIC = Path(__file__).resolve().parent / "static"
API = "https://discord.com/api/v10"
SESSION_DAYS = 7


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


# ------------------------------------------------------------------ config / sessions
class Config:
    def __init__(self, bot):
        self.client_id = os.getenv("DISCORD_CLIENT_ID") or str(bot.application_id)
        self.client_secret = os.getenv("DISCORD_CLIENT_SECRET", "")
        self.base_url = (os.getenv("DASHBOARD_URL") or "http://localhost:8080").rstrip("/")
        self.host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT") or 8080)
        self.secure = self.base_url.startswith("https://")
        p = urlparse(self.base_url)
        self.origin = f"{p.scheme}://{p.netloc}"
        self.secret = self._secret()

    @staticmethod
    def _secret() -> bytes:
        env = os.getenv("SESSION_SECRET")
        if env:
            return env.encode()
        path = DATA / ".session_secret"  # persisted so logins survive restarts
        if not path.exists():
            path.write_text(secrets.token_hex(32))
        return path.read_text().strip().encode()


def sign(payload: dict, secret: bytes) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{raw}.{hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()}"


def unsign(token: str, secret: bytes):
    try:
        raw, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return payload if payload.get("exp", 0) > time.time() else None
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------ middleware
@web.middleware
async def middleware(request: web.Request, handler):
    cfg: Config = request.app["cfg"]
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("Origin") != cfg.origin:
        return web.json_response({"error": "Bad origin."}, status=403)
    try:
        resp = await handler(request)
    except ApiError as e:
        return web.json_response({"error": e.message}, status=e.status)
    except web.HTTPException:
        raise
    except discord.Forbidden:
        return web.json_response({"error": "I don't have permission to do that in Discord "
                                           "(check my permissions and that my role is high enough)."}, status=400)
    except discord.HTTPException as e:
        return web.json_response({"error": f"Discord rejected that: {e.text or e}"}, status=400)
    except Exception:
        log.exception("Dashboard error on %s", request.path)
        return web.json_response({"error": "Something went wrong on the server."}, status=500)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' https: data:; "
        "style-src 'self'; script-src 'self'; frame-ancestors 'none'")
    return resp


# ------------------------------------------------------------------ helpers
def session_user(request):
    return unsign(request.cookies.get("session", ""), request.app["cfg"].secret)


def need_user(request) -> dict:
    user = session_user(request)
    if not user:
        raise ApiError("Please log in.", 401)
    return user


def to_id(value, what="ID") -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        raise ApiError(f"Invalid {what}.")


def sid(value):
    return str(value) if value else None


def can_manage(member: discord.Member, guild: discord.Guild) -> bool:
    p = member.guild_permissions
    return guild.owner_id == member.id or p.administrator or p.manage_guild


async def need_guild(request):
    user = need_user(request)
    bot = request.app["bot"]
    guild = bot.get_guild(to_id(request.match_info["gid"], "server"))
    if not guild:
        raise ApiError("I'm not in that server.", 404)
    uid = int(user["uid"])
    member = guild.get_member(uid)
    if member is None:
        try:
            member = await guild.fetch_member(uid)
        except discord.HTTPException:
            raise ApiError("You're not in that server.", 403)
    if not can_manage(member, guild):
        raise ApiError("You need the Manage Server permission there.", 403)
    return guild, member


async def read_json(request) -> dict:
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        raise ApiError("Invalid request body.")
    if not isinstance(body, dict):
        raise ApiError("Invalid request body.")
    return body


def text_field(body, key, limit, default="") -> str:
    v = body.get(key, default)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ApiError(f"Invalid {key}.")
    v = v.strip()
    if len(v) > limit:
        raise ApiError(f"{key.title()} is too long (max {limit} characters).")
    return v


def url_field(body, key) -> str:
    v = text_field(body, key, 500)
    if v and not v.lower().startswith(("http://", "https://")):
        raise ApiError(f"{key.title()} must be a link starting with http:// or https://")
    return v


def int_field(body, key, lo, hi) -> int:
    v = body.get(key)
    label = key.replace("_", " ").capitalize()
    if isinstance(v, bool) or v in (None, ""):
        raise ApiError(f"{label} must be a number.")
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ApiError(f"{label} must be a number.")
    if not lo <= n <= hi:
        raise ApiError(f"{label} must be between {lo} and {hi}.")
    return n


def get_category(guild, value, what="category"):
    if value in (None, "", 0, "0"):
        return None
    ch = guild.get_channel(to_id(value, what))
    if not isinstance(ch, discord.CategoryChannel):
        raise ApiError(f"Invalid {what}.")
    return ch


def color_field(body, default=BLURPLE, key="color") -> int:
    v = text_field(body, key, 20)
    if not v:
        return default
    c = parse_color(v)
    if c is None or not 0 <= c <= 0xFFFFFF:
        raise ApiError("Color must be a hex value like #5865F2.")
    return c


def get_text_channel(guild, value, what="channel", required=True):
    if value in (None, "", 0, "0"):
        if required:
            raise ApiError(f"Pick a {what}.")
        return None
    ch = guild.get_channel(to_id(value, what))
    if not isinstance(ch, discord.TextChannel):
        raise ApiError(f"Invalid {what}.")
    return ch


def get_role(guild, value, what="role", required=True):
    if value in (None, "", 0, "0"):
        if required:
            raise ApiError(f"Pick a {what}.")
        return None
    role = guild.get_role(to_id(value, what))
    if not role:
        raise ApiError(f"Invalid {what}.")
    return role


def check_assignable(guild, member, role):
    """Same rules as the ; commands: nobody can hand out a role above their own."""
    if role.is_default() or role.managed:
        raise ApiError(f"@{role.name} can't be assigned manually.")
    if role >= guild.me.top_role:
        raise ApiError(f"@{role.name} is above my highest role. Move my role higher in Server Settings → Roles.")
    if member.id != guild.owner_id and role >= member.top_role:
        raise ApiError(f"@{role.name} is above your highest role.")


def check_channel(channel, member, guild, *, send=False):
    mine, yours = channel.permissions_for(guild.me), channel.permissions_for(member)
    if not yours.view_channel or (send and not yours.send_messages):
        raise ApiError(f"You can't {'post in' if send else 'see'} #{channel.name}.", 403)
    if send and not (mine.view_channel and mine.send_messages and mine.embed_links):
        raise ApiError(f"I need View Channel, Send Messages and Embed Links in #{channel.name}.")


def hex_color(value, default=BLURPLE) -> str:
    return f"#{(value if isinstance(value, int) else default):06x}"


def build_embed(spec: dict) -> discord.Embed:
    title = text_field(spec, "title", 256)
    desc = text_field(spec, "description", 4000)
    fields = spec.get("fields") or []
    if not isinstance(fields, list) or len(fields) > 10:
        raise ApiError("At most 10 fields.")
    emb = discord.Embed(title=title or None, description=desc or None, color=color_field(spec))
    for f in fields:
        if not isinstance(f, dict):
            raise ApiError("Invalid field.")
        name, value = text_field(f, "name", 256), text_field(f, "value", 1024)
        if not name or not value:
            raise ApiError("Every field needs a name and a value.")
        emb.add_field(name=name, value=value, inline=bool(f.get("inline")))
    if (img := url_field(spec, "image")):
        emb.set_image(url=img)
    if (thumb := url_field(spec, "thumbnail")):
        emb.set_thumbnail(url=thumb)
    if (footer := text_field(spec, "footer", 2048)):
        emb.set_footer(text=footer)
    if not (title or desc or fields or img):
        raise ApiError("The embed is empty - add a title, description or field.")
    return emb


# ------------------------------------------------------------------ building the settings payload
def build_config(guild: discord.Guild) -> dict:
    c = store.get(guild.id)
    w = c.get("welcome", {})
    a = am(guild.id)
    ar = c.get("autorole", {})

    welcome = {
        "channel": sid(w.get("channel")),
        "message": w.get("message", "Welcome {user} to **{server}**! You are member #{count}."),
        "embed": bool(w.get("embed")),
        "title": w.get("title", ""),
        "color": hex_color(w.get("color")),
        "image": w.get("image", ""),
        "footer": w.get("footer", ""),
        "webhook": bool(w.get("webhook_url")),
        "webhook_name": w.get("webhook_name", ""),
        "webhook_avatar": w.get("webhook_avatar", ""),
    }
    reaction_roles = []
    for mid, entry in rr_data(guild.id).items():
        ch = guild.get_channel(entry.get("channel", 0))
        items = []
        for r in entry["roles"].values():
            role = guild.get_role(r["role"])
            try:
                emoji_url = discord.PartialEmoji.from_str(r["emoji"]).url if "<" in r["emoji"] else None
            except Exception:
                emoji_url = None
            items.append({"emoji": r["emoji"], "emoji_url": emoji_url, "role": sid(r["role"]),
                          "role_name": role.name if role else "(deleted role)"})
        reaction_roles.append({
            "message_id": mid, "channel": sid(entry.get("channel")),
            "channel_name": ch.name if ch else "(deleted channel)",
            "url": f"https://discord.com/channels/{guild.id}/{entry.get('channel')}/{mid}",
            "roles": items})
    lv = lv_cfg(guild.id)
    leveling = {
        "enabled": bool(lv["enabled"]), "announce": lv["announce"], "channel": sid(lv["channel"]),
        "message": lv["message"], "embed": bool(lv["embed"]), "title": lv["title"], "color": hex_color(lv["color"]),
        "image": lv["image"], "footer": lv["footer"],
        "xp_min": lv["xp_min"], "xp_max": lv["xp_max"], "cooldown": lv["cooldown"],
        "ignored_channels": [str(x) for x in lv["ignored_channels"]],
        "ignored_roles": [str(x) for x in lv["ignored_roles"]],
        "rewards": [{"level": int(l), "role": str(rid)} for l, rid in sorted(lv["rewards"].items(), key=lambda kv: int(kv[0]))],
        "stack": bool(lv["stack_rewards"]), "modes": list(ANNOUNCE_MODES),
    }
    tc = ticket_cfg(guild.id)
    tickets = {
        "support_role": sid(tc["support_role"]), "category": sid(tc["category"]), "log_channel": sid(tc["log_channel"]),
        "max_open": tc["max_open"], "styles": list(STYLE_NAMES), "placeholders": PLACEHOLDER_HELP,
        "max_panels": MAX_PANELS, "max_buttons": MAX_BUTTONS, "default_welcome": DEFAULT_WELCOME,
        "panels": [{
            "id": pid, "channel": sid(p["channel"]), "message_id": sid(p["message_id"]),
            "url": (f"https://discord.com/channels/{guild.id}/{p['channel']}/{p['message_id']}" if p["message_id"] else ""),
            "title": p["title"], "description": p["description"], "color": hex_color(p["color"]),
            "image": p["image"], "thumbnail": p["thumbnail"], "footer": p["footer"],
            "buttons": [{
                "id": b["id"], "label": b["label"], "emoji": b["emoji"], "style": b["style"], "name": b["name"],
                "category": sid(b["category"]), "support_role": sid(b["support_role"]), "ping_staff": bool(b["ping_staff"]),
                "welcome_title": b["welcome_title"], "welcome_message": b["welcome_message"],
                "welcome_color": hex_color(b["welcome_color"]),
            } for b in p["buttons"]],
        } for pid, p in tc["panels"].items()],
    }
    return {
        "prefix": c.get("prefix", ";"),
        "tickets": tickets,
        "leveling": leveling,
        "welcome": welcome,
        "autorole": {"humans": sid(ar.get("humans")), "bots": sid(ar.get("bots"))},
        "automod": {
            "antilink": {"on": bool(a["antilink"]["on"]), "action": a["antilink"]["action"],
                         "timeout_minutes": a["antilink"]["timeout_minutes"], "allow_gifs": bool(a["antilink"]["allow_gifs"]),
                         "whitelist": a["antilink"]["whitelist"]},
            "badwords": {"on": bool(a["badwords"]["on"]), "action": a["badwords"]["action"],
                         "timeout_minutes": a["badwords"]["timeout_minutes"], "words": a["badwords"]["words"]},
            "bypass_roles": [str(r) for r in a["bypass_roles"]],
            "bypass_channels": [str(r) for r in a["bypass_channels"]],
            "actions": list(ACTIONS),
        },
        "triggers": [{"phrase": k, "reply": v} for k, v in c.get("triggers", {}).items()],
        "reactionroles": reaction_roles,
    }


def build_guild(guild: discord.Guild, member: discord.Member) -> dict:
    roles = []
    for r in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        if r.is_default():
            continue
        ok = not r.managed and r < guild.me.top_role and (member.id == guild.owner_id or r < member.top_role)
        roles.append({"id": str(r.id), "name": r.name, "color": hex_color(r.color.value or 0x99AAB5), "assignable": ok})
    return {
        "guild": {"id": str(guild.id), "name": guild.name, "icon": guild.icon.url if guild.icon else None},
        "channels": [{"id": str(c.id), "name": c.name} for c in guild.text_channels],
        "categories": [{"id": str(c.id), "name": c.name} for c in guild.categories],
        "emojis": [{"name": e.name, "text": str(e), "url": str(e.url), "animated": e.animated}
                   for e in guild.emojis if e.available][:250],
        "roles": roles,
        "config": build_config(guild),
    }


# ------------------------------------------------------------------ pages / auth
async def index(request):
    return web.FileResponse(STATIC / "index.html")


async def health(request):
    return web.Response(text="ok")


async def login(request):
    cfg: Config = request.app["cfg"]
    state = secrets.token_urlsafe(24)
    url = f"https://discord.com/oauth2/authorize?" + urlencode({
        "client_id": cfg.client_id, "redirect_uri": f"{cfg.base_url}/callback",
        "response_type": "code", "scope": "identify", "state": state, "prompt": "none"})
    resp = web.HTTPFound(url)
    resp.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="Lax", secure=cfg.secure, path="/")
    raise resp


async def callback(request):
    cfg: Config = request.app["cfg"]
    state, code = request.query.get("state", ""), request.query.get("code", "")
    expected = request.cookies.get("oauth_state", "")
    if not code or not expected or not hmac.compare_digest(state.encode(), expected.encode()):
        return web.Response(text="Login failed or expired. Go back and try again: " + cfg.base_url, status=400)
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(f"{API}/oauth2/token", data={
                "client_id": cfg.client_id, "client_secret": cfg.client_secret, "grant_type": "authorization_code",
                "code": code, "redirect_uri": f"{cfg.base_url}/callback"}) as r:
                if r.status != 200:
                    log.warning("OAuth token exchange failed: %s %s", r.status, await r.text())
                    return web.Response(text="Discord login failed. Check DISCORD_CLIENT_SECRET and that the "
                                             f"redirect {cfg.base_url}/callback is added in the Developer Portal.", status=400)
                token = (await r.json())["access_token"]
            async with http.get(f"{API}/users/@me", headers={"Authorization": f"Bearer {token}"}) as r:
                if r.status != 200:
                    return web.Response(text="Could not read your Discord profile.", status=400)
                me = await r.json()
    except (aiohttp.ClientError, KeyError, ValueError):
        return web.Response(text="Could not reach Discord. Try again.", status=502)

    uid = int(me["id"])
    avatar = (f"https://cdn.discordapp.com/avatars/{uid}/{me['avatar']}.png?size=64" if me.get("avatar")
              else f"https://cdn.discordapp.com/embed/avatars/{(uid >> 22) % 6}.png")
    payload = {"uid": str(uid), "name": me.get("global_name") or me.get("username", "user"),
               "avatar": avatar, "exp": int(time.time()) + SESSION_DAYS * 86400}
    resp = web.HTTPFound("/")
    resp.set_cookie("session", sign(payload, cfg.secret), max_age=SESSION_DAYS * 86400, httponly=True,
                    samesite="Lax", secure=cfg.secure, path="/")
    resp.del_cookie("oauth_state", path="/")
    raise resp


async def logout(request):
    resp = web.json_response({"ok": True})
    resp.del_cookie("session", path="/")
    return resp


# ------------------------------------------------------------------ API
async def api_me(request):
    cfg, bot = request.app["cfg"], request.app["bot"]
    user = session_user(request)
    invite = f"https://discord.com/oauth2/authorize?client_id={cfg.client_id}&permissions=8&scope=bot"
    return web.json_response({
        "user": {"name": user["name"], "avatar": user["avatar"]} if user else None,
        "bot": {"name": bot.user.name, "avatar": bot.user.display_avatar.url, "invite": invite}})


async def api_guilds(request):
    user = need_user(request)
    uid, out = int(user["uid"]), []
    for g in request.app["bot"].guilds:
        m = g.get_member(uid)
        if m and can_manage(m, g):
            out.append({"id": str(g.id), "name": g.name, "icon": g.icon.url if g.icon else None})
    out.sort(key=lambda g: g["name"].lower())
    return web.json_response({"guilds": out})


async def api_guild(request):
    guild, member = await need_guild(request)
    return web.json_response(build_guild(guild, member))


async def respond(guild, member):
    return web.json_response(build_guild(guild, member))


async def put_prefix(request):
    guild, member = await need_guild(request)
    prefix = text_field(await read_json(request), "prefix", 5)
    if not prefix or any(ch.isspace() for ch in prefix):
        raise ApiError("The prefix must be 1-5 characters with no spaces.")
    store.get(guild.id)["prefix"] = prefix
    store.save(guild.id)
    return await respond(guild, member)


async def put_welcome(request):
    guild, member = await need_guild(request)
    body = await read_json(request)
    c = store.get(guild.id)
    old = dict(c.get("welcome", {}))
    channel = get_text_channel(guild, body.get("channel"), required=False)
    message = text_field(body, "message", 1500)
    if channel and not message:
        raise ApiError("Write a welcome message.")
    data = {
        "message": message, "embed": bool(body.get("embed")), "title": text_field(body, "title", 256),
        "color": color_field(body), "image": url_field(body, "image"), "footer": text_field(body, "footer", 2048),
        "webhook_name": text_field(body, "webhook_name", 80), "webhook_avatar": url_field(body, "webhook_avatar"),
    }
    if channel:
        check_channel(channel, member, guild, send=True)

    w = {k: v for k, v in old.items() if k in ("webhook_url",)}
    w.update({k: v for k, v in data.items() if v not in ("", None)} | {"embed": data["embed"], "color": data["color"]})
    if channel:
        w["channel"] = channel.id
    if body.get("webhook") and channel:
        if not w.get("webhook_url") or old.get("channel") != channel.id:
            if not channel.permissions_for(guild.me).manage_webhooks:
                raise ApiError("I need the Manage Webhooks permission to use a webhook.")
            hook = await channel.create_webhook(name=data["webhook_name"] or "Welcome", reason="Dashboard welcome webhook")
            w["webhook_url"] = hook.url
    else:
        w.pop("webhook_url", None)
    c["welcome"] = w
    store.save(guild.id)
    return await respond(guild, member)


async def welcome_test(request):
    guild, member = await need_guild(request)
    cog = request.app["bot"].get_cog("Welcome")
    if not store.get(guild.id).get("welcome", {}).get("channel"):
        raise ApiError("Pick a welcome channel and save first.")
    await cog.send_welcome(member)
    return web.json_response({"ok": True})


async def put_autorole(request):
    guild, member = await need_guild(request)
    body = await read_json(request)
    ar = {}
    for key in ("humans", "bots"):
        role = get_role(guild, body.get(key), required=False)
        if role:
            if store.get(guild.id).get("autorole", {}).get(key) != role.id:   # only re-check new/changed roles
                check_assignable(guild, member, role)
            ar[key] = role.id
    store.get(guild.id)["autorole"] = ar
    store.save(guild.id)
    return await respond(guild, member)


async def put_automod(request):
    guild, member = await need_guild(request)
    body = await read_json(request)
    al, bw = body.get("antilink"), body.get("badwords")
    if not isinstance(al, dict) or not isinstance(bw, dict):
        raise ApiError("Invalid request body.")
    for part in (al, bw):
        if part.get("action") not in ACTIONS:
            raise ApiError("Pick a valid action.")

    whitelist = []
    for d in al.get("whitelist") or []:
        d = domain_of(str(d).strip()) if str(d).strip() else ""
        if d and len(d) <= 100 and d not in whitelist:
            whitelist.append(d)
    words = []
    for wd in bw.get("words") or []:
        wd = str(wd).strip().lower()
        if wd and len(wd) <= 100 and wd not in words:
            words.append(wd)
    if len(whitelist) > 200 or len(words) > 500:
        raise ApiError("Too many entries.")

    roles = [get_role(guild, r).id for r in (body.get("bypass_roles") or [])][:50]
    chans = [get_text_channel(guild, ch).id for ch in (body.get("bypass_channels") or [])][:50]

    a = am(guild.id)
    a["antilink"] = {"on": bool(al.get("on")), "action": al["action"], "whitelist": whitelist,
                     "timeout_minutes": int_field(al, "timeout_minutes", 1, 40320), "allow_gifs": bool(al.get("allow_gifs"))}
    a["badwords"] = {"on": bool(bw.get("on")), "action": bw["action"], "words": words,
                     "timeout_minutes": int_field(bw, "timeout_minutes", 1, 40320)}
    a["bypass_roles"], a["bypass_channels"] = roles, chans
    store.save(guild.id)
    return await respond(guild, member)


async def put_leveling(request):
    guild, member = await need_guild(request)
    body = await read_json(request)
    c = lv_cfg(guild.id)

    announce = body.get("announce")
    if announce not in ANNOUNCE_MODES:
        raise ApiError("Pick where level-ups are announced.")
    channel = get_text_channel(guild, body.get("channel"), "announcement channel", required=False)
    if announce == "channel" and not channel:
        raise ApiError("Pick the announcement channel.")
    message = text_field(body, "message", 1500)
    if not message:
        raise ApiError("Write a level-up message.")
    xp_min, xp_max = int_field(body, "xp_min", 1, 500), int_field(body, "xp_max", 1, 500)
    if xp_min > xp_max:
        raise ApiError("XP minimum can't be higher than the maximum.")
    cooldown = int_field(body, "cooldown", 0, 3600)

    ignored_channels = [get_text_channel(guild, x).id for x in (body.get("ignored_channels") or [])][:100]
    ignored_roles = [get_role(guild, x).id for x in (body.get("ignored_roles") or [])][:100]

    raw = body.get("rewards") or []
    if not isinstance(raw, list) or len(raw) > 50:
        raise ApiError("At most 50 rewards.")
    rewards = {}
    for r in raw:
        if not isinstance(r, dict):
            raise ApiError("Invalid reward.")
        level = int_field(r, "level", 1, 500)
        role = get_role(guild, r.get("role"), "reward role")
        if str(level) in rewards:
            raise ApiError(f"Level {level} has two rewards - use one role per level.")
        if c["rewards"].get(str(level)) != role.id:      # only re-check roles that are new or changed
            check_assignable(guild, member, role)
        rewards[str(level)] = role.id

    c.update({
        "enabled": bool(body.get("enabled")), "announce": announce, "channel": channel.id if channel else None,
        "message": message, "embed": bool(body.get("embed")), "title": text_field(body, "title", 256),
        "color": color_field(body), "image": url_field(body, "image"), "footer": text_field(body, "footer", 2048),
        "xp_min": xp_min, "xp_max": xp_max, "cooldown": cooldown,
        "ignored_channels": ignored_channels, "ignored_roles": ignored_roles,
        "rewards": rewards, "stack_rewards": bool(body.get("stack")),
    })
    store.save(guild.id)
    return await respond(guild, member)


async def put_triggers(request):
    guild, member = await need_guild(request)
    items = (await read_json(request)).get("triggers")
    if not isinstance(items, list) or len(items) > 100:
        raise ApiError("Invalid triggers (max 100).")
    triggers = {}
    for it in items:
        if not isinstance(it, dict):
            raise ApiError("Invalid trigger.")
        phrase, reply = text_field(it, "phrase", 100).lower(), text_field(it, "reply", 1500)
        if not phrase or not reply:
            raise ApiError("Every trigger needs a phrase and a reply.")
        triggers[phrase] = reply
    store.get(guild.id)["triggers"] = triggers
    store.save(guild.id)
    return await respond(guild, member)


def clean_button(guild, raw, emojis) -> dict:
    if not isinstance(raw, dict):
        raise ApiError("Invalid button.")
    label = text_field(raw, "label", 80)
    try:
        emoji = normalize_emoji(text_field(raw, "emoji", 64), emojis)
    except ValueError as e:
        raise ApiError(str(e))
    if not label and not emoji:
        raise ApiError("Every button needs a label or an emoji.")
    style = raw.get("style", "blurple")
    if style not in STYLE_NAMES:
        raise ApiError("Pick a valid button color.")
    welcome = text_field(raw, "welcome_message", 1500)
    if not welcome:
        raise ApiError("Write a ticket welcome message for every button.")
    cat, role = get_category(guild, raw.get("category")), get_role(guild, raw.get("support_role"), required=False)
    return {
        "label": label, "emoji": emoji, "style": style,
        "name": clean_channel_part(text_field(raw, "name", 30))[:30] or "ticket",
        "category": cat.id if cat else None, "support_role": role.id if role else None,
        "ping_staff": bool(raw.get("ping_staff")),
        "welcome_title": text_field(raw, "welcome_title", 256), "welcome_message": welcome,
        "welcome_color": color_field(raw, key="welcome_color"),
    }


async def put_tickets(request):
    guild, member = await need_guild(request)
    body = await read_json(request)
    tc = ticket_cfg(guild.id)
    emojis = [(e.name, str(e)) for e in guild.emojis]

    role = get_role(guild, body.get("support_role"), "support role", required=False)
    cat = get_category(guild, body.get("category"))
    log_ch = get_text_channel(guild, body.get("log_channel"), "log channel", required=False)
    max_open = int_field(body, "max_open", 1, 10)

    raw_panels = body.get("panels")
    if not isinstance(raw_panels, list) or len(raw_panels) > MAX_PANELS:
        raise ApiError(f"At most {MAX_PANELS} panels.")
    work = {}                      # panel id -> new panel dict (nothing is saved until everything validated)
    counter, existing = tc["next_panel"], set(tc["panels"])
    for rp in raw_panels:
        if not isinstance(rp, dict):
            raise ApiError("Invalid panel.")
        pid = str(rp.get("id") or "")
        old = tc["panels"].get(pid)
        if pid and not old:
            raise ApiError("A panel was deleted in the meantime - reload the page.", 409)
        if not pid:
            pid, counter = next_id("p", counter, existing | set(work))
        if pid in work:
            raise ApiError("Duplicate panel.")
        channel = get_text_channel(guild, rp.get("channel"), "panel channel", required=False)
        if channel:
            check_channel(channel, member, guild, send=True)
        raw_buttons = rp.get("buttons")
        if not isinstance(raw_buttons, list) or not 1 <= len(raw_buttons) <= MAX_BUTTONS:
            raise ApiError(f"Each panel needs 1-{MAX_BUTTONS} buttons.")
        bcounter = old["next_button"] if old else 2
        old_ids = {b["id"] for b in old["buttons"]} if old else set()
        buttons, used = [], set()
        for rb in raw_buttons:
            btn = clean_button(guild, rb, emojis)
            bid = str(rb.get("id") or "")
            if not bid or bid not in old_ids or bid in used:
                bid, bcounter = next_id("b", bcounter, old_ids | used)
            used.add(bid)
            buttons.append({"id": bid, **btn})
        title, desc = text_field(rp, "title", 256), text_field(rp, "description", 4000)
        image = url_field(rp, "image")
        if not (title or desc or image):
            raise ApiError("Every panel needs a title, description or image.")
        # An already-posted panel keeps its channel here; moving it is done by the "Send / update" button.
        keep_channel = old and old.get("message_id")
        work[pid] = {
            "channel": old["channel"] if keep_channel else (channel.id if channel else None),
            "message_id": old["message_id"] if old else None,
            "title": title, "description": desc, "color": color_field(rp), "image": image,
            "thumbnail": url_field(rp, "thumbnail"), "footer": text_field(rp, "footer", 2048),
            "buttons": buttons, "next_button": bcounter,
        }

    # panels not in the payload are kept - they are only ever removed through the delete endpoint
    for pid, panel in work.items():
        tc["panels"][pid] = panel
    tc.update({"support_role": role.id if role else None, "category": cat.id if cat else None,
               "log_channel": log_ch.id if log_ch else None, "max_open": max_open, "next_panel": counter})
    store.save(guild.id)
    return await respond(guild, member)


async def send_ticket_panel(request):
    guild, member = await need_guild(request)
    pid = request.match_info["pid"]
    if pid not in ticket_cfg(guild.id)["panels"]:
        raise ApiError("That panel no longer exists.", 404)
    body = await read_json(request)
    channel = get_text_channel(guild, body.get("channel"), "panel channel")
    check_channel(channel, member, guild, send=True)
    if not channel.permissions_for(guild.me).read_message_history:
        raise ApiError(f"I need Read Message History in #{channel.name}.")
    try:
        await request.app["bot"].get_cog("Tickets").publish_panel(guild, pid, channel)
    except (ValueError, discord.HTTPException) as e:
        raise ApiError(f"Couldn't post the panel: {e}")
    return await respond(guild, member)


async def delete_ticket_panel(request):
    guild, member = await need_guild(request)
    pid = request.match_info["pid"]
    if pid not in ticket_cfg(guild.id)["panels"]:
        raise ApiError("That panel no longer exists.", 404)
    await request.app["bot"].get_cog("Tickets").remove_panel(guild, pid)
    return await respond(guild, member)


async def post_embed(request):
    guild, member = await need_guild(request)
    body = await read_json(request)
    channel = get_text_channel(guild, body.get("channel"))
    check_channel(channel, member, guild, send=True)
    content = text_field(body, "content", 2000)
    has_embed = bool(body.get("use_embed", True))
    emb = build_embed(body) if has_embed else None
    if not content and not emb:
        raise ApiError("Nothing to send.")
    msg = await channel.send(content=content or None, embed=emb, allowed_mentions=discord.AllowedMentions.none())
    return web.json_response({"ok": True, "url": msg.jump_url})


async def post_reactionrole(request):
    guild, member = await need_guild(request)
    body = await read_json(request)
    channel = get_text_channel(guild, body.get("channel"))
    check_channel(channel, member, guild)
    mine = channel.permissions_for(guild.me)
    if not (mine.add_reactions and mine.read_message_history):
        raise ApiError(f"I need Add Reactions and Read Message History in #{channel.name}.")

    raw = body.get("pairs")
    if not isinstance(raw, list) or not raw or len(raw) > 20:
        raise ApiError("Add between 1 and 20 emoji + role pairs.")
    pairs, seen = [], set()
    for p in raw:
        if not isinstance(p, dict):
            raise ApiError("Invalid pair.")
        try:
            emoji = parse_emoji(str(p.get("emoji", "")))
        except ValueError as e:
            raise ApiError(str(e))
        role = get_role(guild, p.get("role"))
        check_assignable(guild, member, role)
        if emoji_key(emoji) in seen:
            raise ApiError("Each emoji can only be used once per message.")
        seen.add(emoji_key(emoji))
        pairs.append((emoji, role))

    cog = request.app["bot"].get_cog("ReactionRoles")
    mid = str(body.get("message_id") or "").strip()
    if mid:
        try:
            message = await channel.fetch_message(to_id(mid, "message ID"))
        except discord.NotFound:
            raise ApiError("I couldn't find that message in that channel.")
        await cog.attach(message, pairs)
    else:
        spec = body.get("embed") or {}
        if not isinstance(spec, dict):
            raise ApiError("Invalid embed.")
        if not mine.send_messages or not mine.embed_links:
            raise ApiError(f"I need Send Messages and Embed Links in #{channel.name}.")
        message = await channel.send(embed=build_embed(spec), allowed_mentions=discord.AllowedMentions.none())
        try:
            await cog.attach(message, pairs)
        except Exception:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            raise
    return await respond(guild, member)


async def delete_reactionrole(request):
    guild, member = await need_guild(request)
    mid = to_id(request.match_info["mid"], "message")
    cog = request.app["bot"].get_cog("ReactionRoles")
    if not await cog.detach(guild, mid):
        raise ApiError("That reaction role no longer exists.", 404)
    return await respond(guild, member)


# ------------------------------------------------------------------ app
def create_app(bot, cfg: Config) -> web.Application:
    app = web.Application(middlewares=[middleware], client_max_size=256 * 1024)
    app["bot"], app["cfg"] = bot, cfg
    g = "/api/guilds/{gid}"
    app.add_routes([
        web.get("/", index), web.get("/healthz", health),
        web.get("/login", login), web.get("/callback", callback),
        web.post("/api/logout", logout),
        web.get("/api/me", api_me), web.get("/api/guilds", api_guilds),
        web.get(g, api_guild),
        web.put(g + "/prefix", put_prefix),
        web.put(g + "/welcome", put_welcome), web.post(g + "/welcome/test", welcome_test),
        web.put(g + "/autorole", put_autorole),
        web.put(g + "/automod", put_automod),
        web.put(g + "/triggers", put_triggers),
        web.put(g + "/leveling", put_leveling),
        web.put(g + "/tickets", put_tickets),
        web.post(g + "/tickets/panels/{pid}/send", send_ticket_panel),
        web.delete(g + "/tickets/panels/{pid}", delete_ticket_panel),
        web.post(g + "/embed", post_embed),
        web.post(g + "/reactionroles", post_reactionrole),
        web.delete(g + "/reactionroles/{mid}", delete_reactionrole),
        web.static("/static", STATIC),
    ])
    return app


async def start(bot):
    """Start the website. Returns the runner (call runner.cleanup() on shutdown) or None if disabled."""
    cfg = Config(bot)
    if not cfg.client_secret:
        log.info("Dashboard disabled (set DISCORD_CLIENT_SECRET to turn it on).")
        return None
    runner = web.AppRunner(create_app(bot, cfg))
    await runner.setup()
    await web.TCPSite(runner, cfg.host, cfg.port).start()
    log.info("Dashboard running at %s (listening on %s:%d)", cfg.base_url, cfg.host, cfg.port)
    return runner
