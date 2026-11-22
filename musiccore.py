"""Pure music logic (queue, loop modes, progress bar, playback position). No Discord imports -> unit-tested."""
import random
from dataclasses import dataclass

LOOP_MODES = ("off", "track", "queue")
MAX_QUEUE = 500


@dataclass
class Track:
    title: str
    url: str                 # stable page link (the direct stream link expires)
    duration: int = 0        # seconds; 0 = unknown / live
    thumbnail: str = ""
    uploader: str = ""
    requester_id: int = 0
    stream_url: str = ""     # cached direct audio link (short-lived)
    stream_at: float = 0.0


def fmt_time(seconds) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(position: float, total: int, width: int = 16) -> str:
    """▬▬▬🔘▬▬▬▬  1:20 / 3:45   (live streams show LIVE)."""
    if total <= 0:
        return "🔴 **LIVE**" if position <= 0 else f"🔴 **LIVE** · {fmt_time(position)}"
    ratio = min(1.0, max(0.0, position / total))
    knob = min(width - 1, int(ratio * width))
    return "▬" * knob + "🔘" + "▬" * (width - knob - 1) + f"  `{fmt_time(position)} / {fmt_time(total)}`"


class PositionTracker:
    """How far into the current song we are, with pause support. Time is passed in so it can be tested."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.started = None
        self.paused_at = None
        self.paused_total = 0.0

    def start(self, now: float):
        self.started, self.paused_at, self.paused_total = now, None, 0.0

    def pause(self, now: float):
        if self.started is not None and self.paused_at is None:
            self.paused_at = now

    def resume(self, now: float):
        if self.paused_at is not None:
            self.paused_total += now - self.paused_at
            self.paused_at = None

    def position(self, now: float) -> float:
        if self.started is None:
            return 0.0
        end = self.paused_at if self.paused_at is not None else now
        return max(0.0, end - self.started - self.paused_total)


class MusicQueue:
    def __init__(self):
        self.items = []          # upcoming tracks
        self.current = None      # track being played
        self.loop = "off"
        self.volume = 50         # percent, 0-150

    def add(self, tracks) -> int:
        """Append tracks (up to MAX_QUEUE total). Returns how many were added."""
        room = max(0, MAX_QUEUE - len(self.items))
        tracks = list(tracks)[:room]
        self.items.extend(tracks)
        return len(tracks)

    def advance(self, force: bool = False):
        """Choose the track to play next. `force` = the user skipped (a looped single track moves on)."""
        if self.current and self.loop == "track" and not force:
            return self.current
        if self.current and self.loop in ("queue", "track"):
            self.items.append(self.current)   # goes to the back of the line
        self.current = self.items.pop(0) if self.items else None
        return self.current

    def remove(self, position: int):
        """Remove the upcoming track at 1-based `position`; returns it or None."""
        if 1 <= position <= len(self.items):
            return self.items.pop(position - 1)
        return None

    def shuffle(self):
        random.shuffle(self.items)

    def clear(self):
        self.items.clear()

    def cycle_loop(self) -> str:
        self.loop = LOOP_MODES[(LOOP_MODES.index(self.loop) + 1) % len(LOOP_MODES)]
        return self.loop

    def total_seconds(self) -> int:
        return sum(t.duration for t in self.items)
