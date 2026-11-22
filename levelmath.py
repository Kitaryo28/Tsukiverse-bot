"""Pure leveling maths (no Discord imports, so it is easy to test)."""


def xp_needed(level: int) -> int:
    """XP required to go from `level` to `level + 1`."""
    return 5 * level * level + 50 * level + 100


def total_xp_for(level: int) -> int:
    """Total XP at which `level` is reached."""
    return sum(xp_needed(l) for l in range(level))


def level_from_xp(xp: int) -> int:
    level = 0
    while xp >= xp_needed(level):
        xp -= xp_needed(level)
        level += 1
    return level


def progress(xp: int):
    """-> (level, xp into this level, xp needed for next level)."""
    level = level_from_xp(xp)
    return level, xp - total_xp_for(level), xp_needed(level)


def bar(current: int, total: int, width: int = 12) -> str:
    filled = 0 if total <= 0 else max(0, min(width, round(width * current / total)))
    return "▰" * filled + "▱" * (width - filled)


def compact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}"


def fill(text: str, **values) -> str:
    """Replace {placeholders}; unknown ones are left as-is."""
    for key, val in values.items():
        text = text.replace("{" + key + "}", str(val))
    return text
