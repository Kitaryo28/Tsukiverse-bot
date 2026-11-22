"""Link detection for the anti-link filter (pure Python, no Discord imports -> unit-tested)."""
import re
from urllib.parse import urlparse

# Links with a scheme / www / Discord invites, plus bare domains like "example.com" on common endings
# (not preceded by @, so e-mail addresses are not links).
_TLDS = "com|net|org|gg|xyz|io|tv|link|app|dev|info|site|online|store|shop|club|live|fun|top|vip|cc|ly"
LINK_RE = re.compile(
    r"(?:https?://|www\.|discord\.gg/|discord(?:app)?\.com/invite/)[^\s<>()\[\]|]+"
    rf"|(?<![@\w.-])(?:[a-z0-9-]+\.)+(?:{_TLDS})\b(?:/[^\s<>()\[\]|]*)?",
    re.I,
)

# Sites whose links are GIFs (what Discord's GIF picker posts). Extra domains can be added with `antilink whitelist add`.
GIF_DOMAINS = ("tenor.com", "giphy.com", "klipy.com")
GIF_FILE_ENDINGS = (".gif", ".gifv")


def domain_of(link: str) -> str:
    if "://" not in link:
        link = "http://" + link
    host = urlparse(link).netloc.lower().split("@")[-1].split(":")[0]
    return host.removeprefix("www.")


def _matches(domain: str, allowed) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in allowed)


def is_gif_link(link: str) -> bool:
    d = domain_of(link)
    if _matches(d, GIF_DOMAINS):
        return True
    path = urlparse(link if "://" in link else "http://" + link).path.lower()
    return path.endswith(GIF_FILE_ENDINGS)


def find_blocked_link(content: str, whitelist=(), allow_gifs: bool = True):
    """Return the first link in `content` that should be blocked, or None."""
    for raw in LINK_RE.findall(content or ""):
        link = raw.rstrip(".,!?;:'\"")
        d = domain_of(link)
        if not d:
            continue
        if _matches(d, whitelist):
            continue
        if allow_gifs and is_gif_link(link):
            continue
        return link
    return None
