"""Per-server data stored as JSON files on disk (no database needed).

Settings (`store`) are written to disk immediately on every change (atomic write + fsync), so turning the
bot off or restarting it never loses them. Frequently-changing data such as XP (`levels`) is marked dirty and
written in batches every ~30 seconds and on shutdown, so a busy server doesn't hammer the disk.

Set DATA_DIR to put the files somewhere else (e.g. a persistent volume on your host).
"""
import json
import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger("store")

DATA = Path(os.getenv("DATA_DIR") or Path(__file__).resolve().parent.parent / "data").resolve()
DATA.mkdir(parents=True, exist_ok=True)


class Store:
    def __init__(self, kind: str = ""):
        self.kind = kind          # "" -> <id>.json   "levels" -> <id>.levels.json
        self.cache = {}
        self.dirty = set()

    def _path(self, gid) -> Path:
        return DATA / (f"{gid}.{self.kind}.json" if self.kind else f"{gid}.json")

    def _load(self, gid: str) -> dict:
        path = self._path(gid)
        bak = path.with_name(path.name + ".bak")
        for candidate in (path, bak):
            if not candidate.exists():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if candidate != path:
                        log.warning("Recovered %s from backup", path.name)
                    return data
            except (json.JSONDecodeError, OSError):
                pass
            # unreadable file: keep a copy instead of silently overwriting it
            try:
                candidate.replace(candidate.with_name(candidate.name + ".corrupt"))
                log.error("Corrupt data file moved to %s.corrupt", candidate.name)
            except OSError:
                pass
        return {}

    def get(self, guild_id) -> dict:
        gid = str(guild_id)
        if gid not in self.cache:
            self.cache[gid] = self._load(gid)
        return self.cache[gid]

    def save(self, guild_id):
        gid = str(guild_id)
        path = self._path(gid)
        tmp = path.with_name(path.name + ".tmp")
        payload = json.dumps(self.cache.get(gid, {}), indent=None if self.kind else 2, ensure_ascii=False)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            try:
                shutil.copy2(path, path.with_name(path.name + ".bak"))  # keep the last good copy
            except OSError:
                pass
        tmp.replace(path)
        self.dirty.discard(gid)

    def touch(self, guild_id):
        """Mark a server's data as changed; it is written by the next flush_dirty()."""
        self.dirty.add(str(guild_id))

    def flush_dirty(self):
        for gid in list(self.dirty):
            try:
                self.save(gid)
            except OSError:
                log.exception("Could not save %s", gid)

    def flush_all(self):
        """Write every loaded server to disk (called on shutdown)."""
        for gid in list(self.cache):
            try:
                self.save(gid)
            except OSError:
                log.exception("Could not save %s", gid)


store = Store()
levels = Store("levels")
