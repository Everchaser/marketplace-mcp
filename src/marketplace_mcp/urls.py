"""Exact source URLs with a deliberately narrow fetch allowlist."""

import re
from urllib.parse import unquote, urlsplit

BASE = "https://market.yandex.ru"
_CARD = re.compile(r"/(?:card/[^/]+/|product/|product--[^/]+/)[0-9]+\Z")


def validate_url(url: str, *, allow_search=False) -> str:
    if not isinstance(url, str) or not url or len(url) > 8192:
        raise ValueError("Invalid URL")
    if re.search(r"%(?![0-9a-fA-F]{2})", url):
        raise ValueError("Invalid percent escape")
    decoded = url
    for _ in range(4):
        decoded = unquote(decoded, errors="strict")
    if unquote(decoded) != decoded:
        raise ValueError("Excessive nested escaping")
    if any(ord(c) < 32 or ord(c) == 127 or c in "\\#" or c.isspace() for c in decoded):
        raise ValueError("Unsafe URL characters")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc not in {"market.yandex.ru", "market.yandex.ru:443"}
        or parts.hostname != "market.yandex.ru"
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or parts.fragment
    ):
        raise ValueError("Only HTTPS market.yandex.ru:443 is allowed")
    path = unquote(parts.path)
    if any(segment in {".", ".."} for segment in path.split("/")) or unquote(path) != path:
        raise ValueError("Unsafe path")
    if not _CARD.fullmatch(path) and not (allow_search and path == "/search"):
        raise ValueError("Unsupported page path")
    # Encoded separators must not alter routing or reach a different path.
    if path.count("/") != parts.path.count("/"):
        raise ValueError("Encoded path separator")
    return url
