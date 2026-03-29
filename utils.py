import hashlib
import re
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")


def extract_first_url(text: str) -> str | None:
    match = URL_PATTERN.search(text or "")
    return match.group(0) if match else None


def normalize_url(url: str) -> str:
    """Strip tracking params and fragments, lowercase the host."""
    parsed = urlparse(url)
    # Keep only meaningful query params (drop utm_*, fbclid, etc.)
    params = parse_qs(parsed.query)
    filtered = {
        k: v for k, v in params.items()
        if not k.startswith("utm_") and k not in ("fbclid", "igshid", "si", "feature")
    }
    clean = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        fragment="",
        query=urlencode(filtered, doseq=True),
    )
    return urlunparse(clean)


def compute_fingerprint(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode()).hexdigest()
