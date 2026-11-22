"""Ticket data helpers (pure Python, no Discord imports -> unit-tested)."""
import copy
import re

BLURPLE = 0x5865F2
MAX_PANELS = 5
MAX_BUTTONS = 10          # per panel (Discord allows 25, 10 keeps panels tidy)
STYLE_NAMES = ("blurple", "green", "red", "grey")

DEFAULT_WELCOME = ("Hi {user}, thanks for opening a **{type}** ticket!\n"
                   "A staff member will be with you shortly. Please describe your issue below.")

PLACEHOLDER_HELP = "{user} {username} {server} {type} {number}"
CUSTOM_EMOJI = re.compile(r"<a?:\w{2,32}:\d{15,25}>")


def new_button(bid: str, label: str = "Open Ticket", emoji: str = "🎫") -> dict:
    return {
        "id": bid, "label": label, "emoji": emoji, "style": "blurple",
        "name": "ticket",                  # channel name starts with this: ticket-username
        "category": None, "support_role": None, "ping_staff": True,
        "welcome_title": "🎫 Ticket opened", "welcome_message": DEFAULT_WELCOME, "welcome_color": BLURPLE,
    }


def new_panel(channel_id) -> dict:
    return {
        "channel": channel_id, "message_id": None,
        "title": "🎫 Support", "description": "Need help? Press a button below to open a private ticket with our staff.",
        "color": BLURPLE, "image": "", "thumbnail": "", "footer": "",
        "buttons": [new_button("b1")], "next_button": 2,
    }


def default_config() -> dict:
    return {"support_role": None, "category": None, "log_channel": None, "max_open": 1,
            "counter": 0, "panels": {}, "next_panel": 1}


def fill_defaults(cfg: dict) -> dict:
    """Make sure a stored config has every key (old/partial data is upgraded in place)."""
    for key, val in default_config().items():
        cfg.setdefault(key, copy.deepcopy(val))
    for key in ("support_role", "category", "log_channel"):
        if not cfg[key]:                       # legacy 0 -> None
            cfg[key] = None
    for panel in cfg["panels"].values():
        for key, val in new_panel(None).items():
            panel.setdefault(key, copy.deepcopy(val))
        for btn in panel["buttons"]:
            for key, val in new_button(btn["id"]).items():
                btn.setdefault(key, copy.deepcopy(val))
    return cfg


def ticket_owner(topic):
    """Channel topic 'ticket:<user id>|<type>' -> user id, or None if it isn't a ticket channel."""
    if not topic or not topic.startswith("ticket:"):
        return None
    try:
        return int(topic[7:].split("|")[0])
    except ValueError:
        return None


def clean_channel_part(text: str) -> str:
    """Lowercase letters, digits, - and _ only (what Discord allows in a channel name)."""
    text = re.sub(r"[^a-z0-9_-]", "", (text or "").lower().replace(" ", "-"))
    return re.sub(r"-{2,}", "-", text).strip("-_")


def safe_channel_name(prefix: str, username: str, fallback: str) -> str:
    return f"{clean_channel_part(prefix) or 'ticket'}-{clean_channel_part(username) or fallback}"[:90]


def normalize_emoji(text, server_emojis=()) -> str:
    """Accept 🎫, <:name:id>, <a:name:id> or :name: (looked up among the server's emojis).

    server_emojis: iterable of (name, "<:name:id>"). Returns '' for empty / 'none'. Raises ValueError otherwise.
    """
    text = (text or "").strip()
    if not text or text.lower() == "none":
        return ""
    if CUSTOM_EMOJI.fullmatch(text):
        return text
    m = re.fullmatch(r":(\w{2,32}):", text)
    if m:
        for name, full in server_emojis:
            if name.lower() == m[1].lower():
                return full
        raise ValueError(f"This server has no emoji named :{m[1]}:.")
    if len(text) <= 16 and not any(c.isspace() for c in text) and not text.isascii():
        return text
    raise ValueError("Use an emoji like 🎫, a server emoji like :name:, or paste <:name:id>.")


def next_id(prefix: str, counter: int, existing) -> tuple:
    """-> (new id like 'b3', updated counter) never colliding with `existing`."""
    existing = set(existing)
    while f"{prefix}{counter}" in existing:
        counter += 1
    return f"{prefix}{counter}", counter + 1
