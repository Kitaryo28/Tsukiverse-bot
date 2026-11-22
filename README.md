# All-in-One Discord Bot (prefix `;`) + Website Dashboard

Moderation, roles, welcome (plain / embed / webhook), verify, auto-mod (anti-link + bad words), sticky messages,
triggers, embeds, reaction roles, **leveling** (custom level-up embeds + role rewards), **music** (button player),
tickets, utility and an interactive help menu — manageable from commands **or from a website dashboard that works on phones**.

## Your settings are saved (never reset)
Everything is written to disk, one set of files per server in `data/`:
- `<server_id>.json` — all settings (written the instant you change something)
- `<server_id>.levels.json` — XP (written in batches every ~30 s and on shutdown; a hard crash can lose at most the last ~30 s of XP)

Turning the bot off or restarting it loses nothing — it reads those files on start. Writes are atomic with a `.bak`
of the last good copy.

**Keep the `data/` folder on storage that survives restarts.** Own PC/VPS/Raspberry Pi: nothing to do. Docker: use
`-v "$(pwd)/data:/app/data"`. Railway/Render/Fly/Koyeb etc.: attach a **persistent volume** and set `DATA_DIR` to it,
otherwise the disk is wiped on every deploy.

## Setting it up from a phone (no computer)
The bot is just a program that must run 24/7 on some machine. A phone can do *all the setup*; the running part needs a host.

1. **Create the bot (phone browser).** Open https://discord.com/developers/applications (if it looks cramped, use your
   browser's *Desktop site* option). New Application → **Bot** tab → *Reset Token* and copy it → turn on **Server Members
   Intent** and **Message Content Intent**. Then **OAuth2 → URL Generator**: scope `bot`, permission *Administrator*,
   open the link to add the bot to your server.
2. **Get the files onto the host.** Download and unzip this project in your phone's Files app. Then pick one:
   - **A "bot hosting" panel** (many sites offer these; free and paid, quality varies — check reviews and current limits).
     You upload the files in a web file manager, set the start command to `python main.py`, install requirements
     (`pip install -r requirements.txt`) and add your token as an environment variable `DISCORD_TOKEN`.
   - **GitHub + a cloud host** (Railway, Render, Fly.io, Koyeb, …): upload the project to a GitHub repo from your
     phone browser, then create a service from that repo. It will find the `Dockerfile` automatically. Add
     `DISCORD_TOKEN` (and the dashboard variables below) under *Variables*, and **attach a persistent volume at
     `/app/data`**. Pick a "worker/background" type if asked; free plans change often, so check what's offered today.
3. **Manage everything from your phone** with the website dashboard (below) — it's built for small screens.

Just want to try it first? On Android you can run it in the **Termux** app (`pkg install python ffmpeg`, then
`pip install -r requirements.txt`, then `python main.py`). That's for testing — it stops when the phone sleeps or
Termux closes, and some packages (like PyNaCl for music) may need extra steps there.

## Run it yourself (computer / VPS)
```
pip install -r requirements.txt
cp .env.example .env      # paste your token into .env
python main.py
```
Then type `;help`. Needs Python 3.10+. For music also install **ffmpeg** (Docker image already includes it).

## Website dashboard
Log in with Discord, pick a server, and manage: **embed messages, welcome, leveling, reaction roles, auto-mod, auto roles,
triggers and the prefix**. Only people who own the server or have **Manage Server** can open its dashboard, and nobody
can hand out a role above their own.

1. Developer Portal → your app → **OAuth2**: copy the **Client Secret** into `DISCORD_CLIENT_SECRET`.
2. Under **Redirects** add `https://your-address/callback` (or `http://localhost:8080/callback` on your own computer).
3. Set `DASHBOARD_URL` to that same address without `/callback`. Open it in your browser.

When hosted, it must be reachable over **HTTPS** (Railway/Render/Fly give you that; on a VPS put Caddy or nginx in front).
If `DISCORD_CLIENT_SECRET` is empty the website is simply off and everything else works.

## Anti-link
`;antilink on` → every link is deleted instantly and the sender is timed out (default 10 min, change with `;antilink timeout 1h`).
GIF links (Tenor, Giphy, Klipy, `.gif`) are allowed (`;antilink gifs off` to block them too). Allowed sites: `;antilink whitelist add youtube.com`. Check a text with `;antilink test <text>`. Needs the bot to have Manage Messages + Moderate Members and a role above the member.

## Tickets
Dashboard → **Tickets** tab (or `;ticket panel create #channel`): embedded panel with title, description, color, images; up to 10 buttons per panel, each with its own custom emoji, color, category, support role and welcome message.
Welcome placeholders: `{user} {username} {server} {type} {number}`. Tickets have Close (with confirm) + Claim buttons, optional log channel with transcript.

## Hosting notes (Render / Cloudflare)
- Cloudflare Workers can't run this bot. Cloudflare Containers have temporary disk, so settings would reset - not recommended.
- Render: create a **Web Service** (the dashboard listens on `PORT`) and attach a persistent disk mounted at `/app/data`. Free plans may sleep and disks may be paid - check Render's current plans. Railway / Fly.io / Koyeb / a VPS also work.
- Put the token only in the host's Variables as `DISCORD_TOKEN`. Never share it; if it leaked, reset it in the Developer Portal.

## Leveling
Off by default. Turn it on with `;leveling on` or the **Leveling** tab. Members earn 15–25 XP per message (once a minute).
- **Custom level-up message** — plain text or an embed (title, color, image, footer). Placeholders: `{user}` `{username}`
  `{level}` `{xp}` `{server}`. The dashboard shows a live preview; `;leveling preview` shows it in Discord.
- Announce in the same channel, one chosen channel, DMs, or not at all.
- **Role rewards** (`;leveling reward add 10 @Active`) — keep every earned reward, or only the highest.
- Ignore channels/roles, change XP range and cooldown, `;rank`, `;leaderboard` (with page buttons).

## Music
`;play <song name / YouTube link / playlist link>` → a now-playing card with buttons: ⏸ pause · ⏭ skip · ⏹ stop ·
🔁 loop (off/track/queue) · 🔀 shuffle · 🔉🔊 volume · 📜 queue. It leaves by itself when the queue ends or everyone
leaves. Requires ffmpeg (+ the packages in `requirements.txt`).

Honest limits: audio comes from YouTube via `yt-dlp`, and YouTube often **blocks cloud/datacenter IP addresses**
("sign in to confirm you're not a bot"). It works most reliably when the bot runs from a home connection (PC,
Raspberry Pi). On a hosted server you may need to set `YTDLP_COOKIES` (see `.env.example`), and it can still break
until `yt-dlp` is updated (`pip install -U yt-dlp`). Using YouTube this way is against YouTube's terms, which is why large
public music bots have been shut down — keep it to your own server. Spotify/Apple Music links are not supported.
If music can't load, the rest of the bot still runs normally.

## Keep it online 24/7
**Docker (recommended):**
```
docker build -t allinone .
docker run -d --restart unless-stopped --env-file .env -p 8080:8080 -v "$(pwd)/data:/app/data" --name allinone allinone
```
**systemd:** create `/etc/systemd/system/allinone.service`
```
[Unit]
Description=All-in-One Discord Bot
After=network-online.target

[Service]
WorkingDirectory=/home/ubuntu/allinone
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=5
EnvironmentFile=/home/ubuntu/allinone/.env

[Install]
WantedBy=multi-user.target
```
then `sudo systemctl enable --now allinone`.

## Commands (default prefix `;`, change with `;prefix <new>` or on the website)
| Module | Commands |
|---|---|
| Moderation | `ban` `unban` `kick` `mute <time>` `unmute` `warn` `warnings` `clearwarns` `purge` `lock` `unlock` `slowmode` |
| Roles | `addrole` `removerole` `temprole @user @role 1h` `autorole humans/bots @role` `roleall [humans/bots] @role` `removeroleall [humans/bots] @role` |
| Leveling | `rank [@user]` `leaderboard` · admin: `leveling on/off/channel/announce/message/embed/title/color/image/footer/xp/cooldown/ignore/preview/setlevel/addxp/resetxp` · `leveling reward add/remove/list/stack` |
| Music | `play` `pause` `resume` `skip` `stop` `queue` `nowplaying` `volume` `loop` `shuffle` `remove <n>` `clear` `join` `leave` |
| Reaction roles | `reactionrole add <message id/link> <emoji> @role` `reactionrole remove <message> [emoji]` `reactionrole list` (alias `rr`) |
| Welcome | `welcome channel/message/embed/title/color/image/footer/webhook/webhookname/webhookavatar/test/disable` (placeholders `{user} {username} {server} {count}`) |
| Verify | `verify setup #channel @role` `verify unverified @role` |
| AutoMod | `antilink on/off/timeout <10m>/gifs on|off/action/whitelist/test <text>` `badword add/remove/list/on/off/action` `automod bypassrole/bypasschannel` (actions: delete, warn, timeout, kick, ban) |
| Sticky | `sticky <text>` `unsticky` |
| Triggers | `trigger add "phrase" reply` `trigger remove` `trigger list` |
| Embeds | `say [#ch] text` `embed [#ch] title \| description \| #color \| image \| footer` `editembed <msg_id> ...` |
| Tickets | `ticket setup #channel` `ticket panel create/list/send/delete/set` `ticket button add/remove/set` (emoji, label, style, category, role, welcome message) `ticket supportrole/category/logchannel/maxopen` `ticket add/remove/close` |
| Utility | `ping` `av [@user]` `userinfo` `serverinfo` `botinfo` `prefix [new]` `settings` |
| Help | `help` (menu) or `help <command>` |

Durations: `30s`, `10m`, `2h`, `1d`, `1w`.

## Security
Never share or commit your token, client secret, cookies file or `.env`. If one leaks, reset it in the Developer Portal.
