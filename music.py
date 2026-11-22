"""Music player: yt-dlp + FFmpeg, one queue per server, modern now-playing card with buttons.

Needs: PyNaCl (pip), yt-dlp (pip) and the ffmpeg program installed on the machine.
"""
import asyncio
import logging
import os
import shutil
import time

import discord
from discord.ext import commands

try:
    import nacl  # noqa: F401  (voice encryption; discord.py[voice])
    import yt_dlp
except ImportError as exc:  # main.py treats this cog as optional
    raise RuntimeError(f"Music needs PyNaCl and yt-dlp installed ({exc}). Run: pip install -r requirements.txt") from exc

from utils.helpers import BLURPLE, err, ok
from utils.musiccore import LOOP_MODES, MAX_QUEUE, MusicQueue, PositionTracker, Track, fmt_time, progress_bar

log = logging.getLogger("music")

FFMPEG = os.getenv("FFMPEG_PATH", "ffmpeg")
FFMPEG_OPTS = {"before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", "options": "-vn"}
IDLE_LEAVE_SECONDS = 180
ALONE_LEAVE_SECONDS = 60
LOOP_ICON = {"off": "➡️", "track": "🔂", "queue": "🔁"}


def ytdl_options(**extra) -> dict:
    opts = {"format": "bestaudio/best", "quiet": True, "no_warnings": True, "noplaylist": True,
            "default_search": "ytsearch", "source_address": "0.0.0.0", "socket_timeout": 15}
    cookies = os.getenv("YTDLP_COOKIES")  # optional cookies.txt, helps when YouTube blocks server IPs
    if cookies and os.path.exists(cookies):
        opts["cookiefile"] = cookies
    opts.update(extra)
    return opts


def entry_to_track(e: dict, requester_id: int) -> Track | None:
    url = e.get("webpage_url") or e.get("url") or ""
    if not url.startswith("http"):
        return None
    thumb = e.get("thumbnail") or ((e.get("thumbnails") or [{}])[-1].get("url", ""))
    return Track(title=e.get("title") or "Unknown title", url=url, duration=int(e.get("duration") or 0),
                 thumbnail=thumb or "", uploader=e.get("uploader") or e.get("channel") or "", requester_id=requester_id)


class GuildPlayer:
    def __init__(self, guild: discord.Guild):
        self.guild = guild
        self.queue = MusicQueue()
        self.pos = PositionTracker()
        self.lock = asyncio.Lock()
        self.text_channel = None
        self.np_message = None
        self.force_next = False        # a skip while "loop track" is on must still move on
        self.quiet_end = False         # don't say "queue finished" after /stop
        self.idle_task = None
        self.alone_task = None

    def position(self) -> float:
        return self.pos.position(time.monotonic())

    def cancel_timers(self):
        for name in ("idle_task", "alone_task"):
            task = getattr(self, name)
            if task and not task.done():
                task.cancel()
            setattr(self, name, None)


def now_playing_embed(player: GuildPlayer) -> discord.Embed:
    q, t = player.queue, player.queue.current
    emb = discord.Embed(title=t.title[:256], url=t.url, color=BLURPLE, description=progress_bar(player.position(), t.duration))
    emb.set_author(name="Now playing ♪")
    if t.thumbnail:
        emb.set_thumbnail(url=t.thumbnail)
    emb.add_field(name="Requested by", value=f"<@{t.requester_id}>")
    if t.uploader:
        emb.add_field(name="Channel", value=t.uploader[:100])
    emb.add_field(name="Volume", value=f"{q.volume}%")
    emb.add_field(name="Loop", value=f"{LOOP_ICON[q.loop]} {q.loop}")
    up = q.items[0].title[:60] if q.items else "Nothing — add more with `play`"
    emb.add_field(name=f"Up next ({len(q.items)} in queue)", value=up, inline=False)
    return emb


def queue_embed(player: GuildPlayer) -> discord.Embed:
    q = player.queue
    lines = []
    if q.current:
        lines.append(f"**Now:** [{q.current.title[:70]}]({q.current.url}) `{fmt_time(q.current.duration)}`")
    for i, t in enumerate(q.items[:10], start=1):
        lines.append(f"`{i}.` [{t.title[:60]}]({t.url}) `{fmt_time(t.duration)}`")
    if len(q.items) > 10:
        lines.append(f"…and **{len(q.items) - 10}** more")
    emb = discord.Embed(title="Queue", description="\n".join(lines) or "The queue is empty.", color=BLURPLE)
    emb.set_footer(text=f"{len(q.items)} up next · {fmt_time(q.total_seconds())} · loop: {q.loop} · volume {q.volume}%")
    return emb


class PlayerView(discord.ui.View):
    """Buttons under the now-playing card."""

    def __init__(self, cog: "Music", player: GuildPlayer):
        super().__init__(timeout=None)
        self.cog, self.player = cog, player
        self._labels()

    def _labels(self):
        vc = self.player.guild.voice_client
        paused = bool(vc and vc.is_paused())
        self.pause_btn.label, self.pause_btn.emoji = ("Resume", "▶️") if paused else ("Pause", "⏸️")
        self.loop_btn.label, self.loop_btn.emoji = f"Loop: {self.player.queue.loop}", LOOP_ICON[self.player.queue.loop]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        vc = self.player.guild.voice_client
        voice = getattr(interaction.user, "voice", None)
        if not vc or not voice or voice.channel != vc.channel:
            await interaction.response.send_message("Join my voice channel to use the controls.", ephemeral=True)
            return False
        if not self.player.queue.current:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction):
        self._labels()
        await interaction.response.edit_message(embed=now_playing_embed(self.player), view=self)

    @discord.ui.button(label="Pause", emoji="⏸️", style=discord.ButtonStyle.primary, row=0)
    async def pause_btn(self, interaction, button):
        vc = self.player.guild.voice_client
        if vc.is_paused():
            vc.resume()
            self.player.pos.resume(time.monotonic())
        elif vc.is_playing():
            vc.pause()
            self.player.pos.pause(time.monotonic())
        await self._refresh(interaction)

    @discord.ui.button(label="Skip", emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def skip_btn(self, interaction, button):
        self.player.force_next = True
        self.player.guild.voice_client.stop()
        await interaction.response.send_message(f"⏭️ Skipped by {interaction.user.mention}", ephemeral=False,
                                                allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(self, interaction, button):
        self.cog.stop_player(self.player)
        await interaction.response.edit_message(view=None)

    @discord.ui.button(label="Loop", emoji="➡️", style=discord.ButtonStyle.secondary, row=0)
    async def loop_btn(self, interaction, button):
        self.player.queue.cycle_loop()
        await self._refresh(interaction)

    @discord.ui.button(label="Shuffle", emoji="🔀", style=discord.ButtonStyle.secondary, row=0)
    async def shuffle_btn(self, interaction, button):
        self.player.queue.shuffle()
        await self._refresh(interaction)

    @discord.ui.button(label="Vol −", emoji="🔉", style=discord.ButtonStyle.secondary, row=1)
    async def vol_down(self, interaction, button):
        self.cog.set_volume(self.player, self.player.queue.volume - 10)
        await self._refresh(interaction)

    @discord.ui.button(label="Vol +", emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def vol_up(self, interaction, button):
        self.cog.set_volume(self.player, self.player.queue.volume + 10)
        await self._refresh(interaction)

    @discord.ui.button(label="Queue", emoji="📜", style=discord.ButtonStyle.secondary, row=1)
    async def queue_btn(self, interaction, button):
        await interaction.response.send_message(embed=queue_embed(self.player), ephemeral=True)


class Music(commands.Cog, name="Music"):
    """Play music from YouTube links, playlists or search, with a button player."""

    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    async def cog_load(self):
        if not shutil.which(FFMPEG):
            log.warning("ffmpeg was not found (looked for %r). Music commands will say so until it is installed.", FFMPEG)

    async def cog_unload(self):
        for guild_id, player in list(self.players.items()):
            player.cancel_timers()
            player.queue.clear()
            player.queue.current = None
            vc = player.guild.voice_client
            if vc:
                await vc.disconnect(force=True)
        self.players.clear()

    # ------------------------------------------------------------ helpers
    def get_player(self, guild: discord.Guild) -> GuildPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = GuildPlayer(guild)
        return self.players[guild.id]

    def set_volume(self, player: GuildPlayer, value: int):
        player.queue.volume = max(0, min(150, value))
        vc = player.guild.voice_client
        if vc and vc.source and hasattr(vc.source, "volume"):
            vc.source.volume = player.queue.volume / 100

    def stop_player(self, player: GuildPlayer):
        player.queue.clear()
        player.queue.current = None
        player.queue.loop = "off"
        vc = player.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            player.quiet_end = True   # the stop below ends playback; don't announce "queue finished"
            vc.stop()

    async def disconnect_guild(self, guild: discord.Guild):
        player = self.players.pop(guild.id, None)
        if player:
            player.cancel_timers()
            player.queue.clear()
            player.queue.current = None
            player.quiet_end = True
        vc = guild.voice_client
        if vc:
            await vc.disconnect(force=True)

    async def ensure_voice(self, ctx) -> GuildPlayer | None:
        voice = ctx.author.voice
        if not voice or not voice.channel:
            await ctx.send(embed=err("Join a voice channel first."))
            return None
        channel = voice.channel
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.connect and perms.speak):
            await ctx.send(embed=err(f"I need **Connect** and **Speak** in {channel.mention}."))
            return None
        if not shutil.which(FFMPEG):
            await ctx.send(embed=err("ffmpeg isn't installed on the machine running me, so I can't play audio. See the README."))
            return None
        vc = ctx.guild.voice_client
        try:
            if vc is None:
                await channel.connect(self_deaf=True)
            elif vc.channel != channel:
                if vc.is_playing() or vc.is_paused():
                    await ctx.send(embed=err(f"I'm already playing in {vc.channel.mention}."))
                    return None
                await vc.move_to(channel)
        except (asyncio.TimeoutError, discord.ClientException) as exc:
            await ctx.send(embed=err(f"Couldn't join the voice channel: {exc}"))
            return None
        player = self.get_player(ctx.guild)
        player.cancel_timers()
        player.text_channel = ctx.channel
        return player

    def control_check(self, ctx):
        """Player for a control command, or an error string."""
        vc = ctx.guild.voice_client
        player = self.players.get(ctx.guild.id)
        if not vc or not player:
            return None, "I'm not playing anything."
        if not ctx.author.voice or ctx.author.voice.channel != vc.channel:
            return None, "Join my voice channel to control the music."
        return player, None

    async def resolve(self, query: str, requester_id: int):
        loop = asyncio.get_running_loop()
        is_url = query.startswith(("http://", "https://"))
        # a link to one video that merely sits inside a playlist/mix plays just that video
        playlist = is_url and ("/playlist" in query or ("list=" in query and "v=" not in query))

        def work():
            opts = ytdl_options(noplaylist=False, extract_flat="in_playlist", playlistend=50) if playlist else ytdl_options()
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(query if is_url else f"ytsearch1:{query}", download=False)

        info = await loop.run_in_executor(None, work)
        if not info:
            return [], False
        entries = [e for e in (info.get("entries") or []) if e] if info.get("entries") is not None else [info]
        tracks = []
        for e in entries:
            t = entry_to_track(e, requester_id)
            if t:
                if len(entries) == 1 and e.get("url", "").startswith("http") and not playlist:
                    t.stream_url, t.stream_at = e["url"], time.time()   # saves a second lookup right before playing
                tracks.append(t)
        return tracks, playlist

    async def stream_for(self, track: Track) -> str:
        if track.stream_url and time.time() - track.stream_at < 900:
            return track.stream_url
        loop = asyncio.get_running_loop()

        def work():
            with yt_dlp.YoutubeDL(ytdl_options()) as ydl:
                return ydl.extract_info(track.url, download=False)

        info = await loop.run_in_executor(None, work)
        if info.get("entries"):
            info = info["entries"][0]
        track.stream_url, track.stream_at = info["url"], time.time()
        if not track.duration:
            track.duration = int(info.get("duration") or 0)
        return track.stream_url

    # ------------------------------------------------------------ playback engine
    async def play_next(self, player: GuildPlayer, force: bool = False):
        async with player.lock:
            while True:
                vc = player.guild.voice_client
                if not vc or vc.is_playing() or vc.is_paused():   # something is already playing: never start a second stream
                    return
                previous = player.queue.current
                track = player.queue.advance(force=force)
                repeat = track is previous and not force   # "loop track": same song again
                force = False
                if track is None:
                    player.pos.reset()
                    await self._finished(player)
                    return
                try:
                    url = await self.stream_for(track)
                except Exception as exc:  # noqa: BLE001 - yt-dlp raises many kinds of errors
                    log.warning("Could not load %s: %s", track.url, exc)
                    await self._say(player, err(f"Couldn't play **{track.title[:80]}** — {self._why(exc)}. Skipping."))
                    continue
                source = discord.PCMVolumeTransformer(
                    discord.FFmpegPCMAudio(url, executable=FFMPEG, **FFMPEG_OPTS), volume=player.queue.volume / 100)
                vc.play(source, after=lambda e, p=player: self._after(p, e))
                player.pos.start(time.monotonic())
                if not (repeat and player.np_message):   # don't re-post the card on every repeat
                    await self._post_now_playing(player)
                return

    def _after(self, player: GuildPlayer, error):
        """Runs in FFmpeg's thread when a song ends (or is stopped): hop back onto the bot's event loop."""
        if error:
            log.warning("Playback error: %s", error)
        force, player.force_next = player.force_next, False
        asyncio.run_coroutine_threadsafe(self._safe_next(player, force), self.bot.loop)

    async def _safe_next(self, player: GuildPlayer, force: bool):
        try:
            await self.play_next(player, force)
        except Exception:  # noqa: BLE001
            log.exception("play_next failed")

    async def _finished(self, player: GuildPlayer):
        await self._clear_old_card(player)
        if not player.quiet_end:
            await self._say(player, discord.Embed(description="✅ Queue finished. Add more with `play`.", color=BLURPLE))
        player.quiet_end = False
        player.cancel_timers()
        player.idle_task = asyncio.create_task(self._leave_later(player, IDLE_LEAVE_SECONDS, idle=True))

    async def _leave_later(self, player: GuildPlayer, delay: int, idle: bool):
        try:
            await asyncio.sleep(delay)
            vc = player.guild.voice_client
            if not vc:
                return
            if idle and (vc.is_playing() or vc.is_paused() or player.queue.current):
                return
            await self._say(player, discord.Embed(description="👋 Leaving the voice channel" + (" — nothing left to play." if idle else " — nobody is listening."), color=BLURPLE))
            await self.disconnect_guild(player.guild)
        except asyncio.CancelledError:
            pass

    async def _say(self, player: GuildPlayer, embed: discord.Embed):
        if player.text_channel:
            try:
                await player.text_channel.send(embed=embed)
            except discord.HTTPException:
                pass

    async def _clear_old_card(self, player: GuildPlayer):
        if player.np_message:
            try:
                await player.np_message.edit(view=None)
            except discord.HTTPException:
                pass
            player.np_message = None

    async def _post_now_playing(self, player: GuildPlayer):
        await self._clear_old_card(player)
        if player.text_channel:
            try:
                player.np_message = await player.text_channel.send(embed=now_playing_embed(player), view=PlayerView(self, player))
            except discord.HTTPException:
                pass

    @staticmethod
    def _why(exc: Exception) -> str:
        text = str(exc).lower()
        if "sign in" in text or "bot" in text and "confirm" in text:
            return "YouTube is blocking this server's IP (see README: cookies / run it from home)"
        if "private" in text or "unavailable" in text or "removed" in text:
            return "that video is unavailable"
        if "age" in text:
            return "that video is age-restricted"
        if "copyright" in text or "blocked" in text:
            return "that video is blocked here"
        return "it couldn't be loaded"

    # ------------------------------------------------------------ commands
    @commands.command(aliases=["p"], help="Play a song: play <name, YouTube link or playlist link>")
    @commands.bot_has_permissions(embed_links=True)
    async def play(self, ctx, *, query: str):
        player = await self.ensure_voice(ctx)
        if not player:
            return
        async with ctx.typing():
            try:
                tracks, is_playlist = await self.resolve(query.strip("<>"), ctx.author.id)
            except yt_dlp.utils.DownloadError as exc:
                return await ctx.send(embed=err(f"I couldn't load that — {self._why(exc)}."))
            except Exception:  # noqa: BLE001
                log.exception("resolve failed")
                return await ctx.send(embed=err("Something went wrong while searching. Try again."))
        if not tracks:
            return await ctx.send(embed=err("I found nothing for that."))

        was_idle = player.queue.current is None
        added = player.queue.add(tracks)
        if not added:
            return await ctx.send(embed=err(f"The queue is full ({MAX_QUEUE} tracks)."))
        if is_playlist:
            emb = discord.Embed(description=f"📃 Added **{added}** tracks from the playlist.", color=BLURPLE)
        else:
            t = tracks[0]
            emb = discord.Embed(title=t.title[:256], url=t.url, color=BLURPLE)
            emb.set_author(name="Added to queue" if not was_idle else "Starting")
            if t.thumbnail:
                emb.set_thumbnail(url=t.thumbnail)
            emb.add_field(name="Length", value=fmt_time(t.duration) if t.duration else "live")
            emb.add_field(name="Position", value="up next" if was_idle else str(len(player.queue.items)))
        await ctx.send(embed=emb)
        if was_idle and not (ctx.guild.voice_client.is_playing() or ctx.guild.voice_client.is_paused()):
            await self.play_next(player)

    @commands.command(help="Make me join your voice channel.")
    async def join(self, ctx):
        if await self.ensure_voice(ctx):
            await ctx.send(embed=ok(f"Joined {ctx.author.voice.channel.mention}."))

    @commands.command(aliases=["dc", "disconnect"], help="Stop the music and leave the voice channel.")
    async def leave(self, ctx):
        player, problem = self.control_check(ctx)
        if problem:
            return await ctx.send(embed=err(problem))
        await self.disconnect_guild(ctx.guild)
        await ctx.send(embed=ok("Left the voice channel."))

    @commands.command(help="Pause the music.")
    async def pause(self, ctx):
        player, problem = self.control_check(ctx)
        vc = ctx.guild.voice_client
        if problem or not vc.is_playing():
            return await ctx.send(embed=err(problem or "Nothing is playing."))
        vc.pause()
        player.pos.pause(time.monotonic())
        await ctx.send(embed=ok("⏸️ Paused."))

    @commands.command(help="Resume the music.")
    async def resume(self, ctx):
        player, problem = self.control_check(ctx)
        vc = ctx.guild.voice_client
        if problem or not vc.is_paused():
            return await ctx.send(embed=err(problem or "Nothing is paused."))
        vc.resume()
        player.pos.resume(time.monotonic())
        await ctx.send(embed=ok("▶️ Resumed."))

    @commands.command(aliases=["s", "next"], help="Skip the current song.")
    async def skip(self, ctx):
        player, problem = self.control_check(ctx)
        if problem or not player.queue.current:
            return await ctx.send(embed=err(problem or "Nothing is playing."))
        player.force_next = True
        ctx.guild.voice_client.stop()
        await ctx.send(embed=ok("⏭️ Skipped."))

    @commands.command(help="Stop playing and clear the queue.")
    async def stop(self, ctx):
        player, problem = self.control_check(ctx)
        if problem:
            return await ctx.send(embed=err(problem))
        self.stop_player(player)
        await ctx.send(embed=ok("⏹️ Stopped and cleared the queue."))

    @commands.command(aliases=["np", "now"], help="Show what's playing.")
    async def nowplaying(self, ctx):
        player = self.players.get(ctx.guild.id)
        if not player or not player.queue.current:
            return await ctx.send(embed=err("Nothing is playing."))
        await ctx.send(embed=now_playing_embed(player))

    @commands.command(aliases=["q"], help="Show the queue.")
    async def queue(self, ctx):
        player = self.players.get(ctx.guild.id)
        if not player or not (player.queue.current or player.queue.items):
            return await ctx.send(embed=err("The queue is empty."))
        await ctx.send(embed=queue_embed(player))

    @commands.command(aliases=["vol"], help="Set or show the volume (0-150).")
    async def volume(self, ctx, level: int = None):
        player, problem = self.control_check(ctx)
        if problem:
            return await ctx.send(embed=err(problem))
        if level is None:
            return await ctx.send(embed=ok(f"🔊 Volume is **{player.queue.volume}%**."))
        if not 0 <= level <= 150:
            return await ctx.send(embed=err("Choose a volume from 0 to 150."))
        self.set_volume(player, level)
        await ctx.send(embed=ok(f"🔊 Volume set to **{level}%**."))

    @commands.command(help="Loop: off, track or queue (no argument cycles through them).")
    async def loop(self, ctx, mode: str = None):
        player, problem = self.control_check(ctx)
        if problem:
            return await ctx.send(embed=err(problem))
        if mode is None:
            player.queue.cycle_loop()
        elif mode.lower() in LOOP_MODES:
            player.queue.loop = mode.lower()
        else:
            return await ctx.send(embed=err("Use `off`, `track` or `queue`."))
        await ctx.send(embed=ok(f"{LOOP_ICON[player.queue.loop]} Loop: **{player.queue.loop}**."))

    @commands.command(help="Shuffle the queue.")
    async def shuffle(self, ctx):
        player, problem = self.control_check(ctx)
        if problem:
            return await ctx.send(embed=err(problem))
        player.queue.shuffle()
        await ctx.send(embed=ok(f"🔀 Shuffled {len(player.queue.items)} tracks."))

    @commands.command(help="Remove a track from the queue by its number.")
    async def remove(self, ctx, number: int):
        player, problem = self.control_check(ctx)
        if problem:
            return await ctx.send(embed=err(problem))
        track = player.queue.remove(number)
        if not track:
            return await ctx.send(embed=err("There's no track with that number (see `queue`)."))
        await ctx.send(embed=ok(f"Removed **{track.title[:80]}**."))

    @commands.command(help="Clear the queue (keeps the current song).")
    async def clear(self, ctx):
        player, problem = self.control_check(ctx)
        if problem:
            return await ctx.send(embed=err(problem))
        player.queue.clear()
        await ctx.send(embed=ok("🧹 Queue cleared."))

    # ------------------------------------------------------------ housekeeping
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild = member.guild
        player = self.players.get(guild.id)
        if member.id == self.bot.user.id and after.channel is None:   # kicked / disconnected
            if player:
                player.cancel_timers()
                self.players.pop(guild.id, None)
            return
        vc = guild.voice_client
        if not vc or not player or not vc.channel:
            return
        if any(not m.bot for m in vc.channel.members):
            if player.alone_task and not player.alone_task.done():
                player.alone_task.cancel()
            player.alone_task = None
        elif player.alone_task is None or player.alone_task.done():
            player.alone_task = asyncio.create_task(self._leave_later(player, ALONE_LEAVE_SECONDS, idle=False))


async def setup(bot):
    await bot.add_cog(Music(bot))
