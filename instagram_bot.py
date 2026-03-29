import json
import logging
import os
import time
from itertools import cycle
from pathlib import Path

from instagrapi import Client as IGClient
from instagrapi.exceptions import (
    ChallengeRequired,
    FeedbackRequired,
    PleaseWaitFewMinutes,
    RateLimitError,
)

import analyzer
import db
import utils

logger = logging.getLogger(__name__)

IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")
IG_SESSION_FILE = os.getenv("IG_SESSION_FILE", "./session.json")
USE_PROXY = os.getenv("USE_PROXY", "false").lower() == "true"
IG_PROXY_LIST = [p.strip() for p in os.getenv("IG_PROXY_LIST", "").split(",") if p.strip()]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))

# Round-robin proxy rotation
_proxy_cycle = cycle(IG_PROXY_LIST) if IG_PROXY_LIST else None
_current_proxy: str | None = None

RATE_LIMIT_ERRORS = (RateLimitError, PleaseWaitFewMinutes, FeedbackRequired, ChallengeRequired)


def next_proxy() -> str | None:
    """Rotate to the next proxy in the list."""
    global _current_proxy
    if not USE_PROXY or not _proxy_cycle:
        return None
    _current_proxy = next(_proxy_cycle)
    return _current_proxy


def create_client(proxy: str | None = None) -> IGClient:
    cl = IGClient()
    p = proxy or next_proxy()
    if p:
        logger.info(f"Using proxy: {p}")
        cl.set_proxy(p)

    session_path = Path(IG_SESSION_FILE)
    if session_path.exists():
        logger.info("Loading saved session...")
        cl.load_settings(session_path)
        cl.login(IG_USERNAME, IG_PASSWORD)
        cl.get_timeline_feed()  # validate session
        logger.info("Session restored successfully")
    else:
        logger.info("Logging in fresh...")
        cl.login(IG_USERNAME, IG_PASSWORD)
        cl.dump_settings(session_path)
        logger.info("Session saved")

    return cl


def rotate_on_rate_limit(cl: IGClient) -> IGClient:
    """Switch to the next proxy and re-create the client."""
    new_proxy = next_proxy()
    if new_proxy:
        logger.warning(f"Rate limited — rotating to proxy: {new_proxy}")
        return create_client(proxy=new_proxy)
    else:
        logger.warning("Rate limited but no proxies available — waiting 60s")
        time.sleep(60)
        return cl


def format_reply(result: dict, is_duplicate: bool) -> str:
    reasons = json.loads(result["reasons_json"]) if isinstance(result["reasons_json"], str) else result["reasons_json"]
    lines = [
        f"🔍 OpenFeed Analysis",
        f"",
        f"Verdict: {result['verdict']}",
        f"Confidence: {result['confidence']}%",
        f"",
        f"Reasons:",
    ]
    for i, r in enumerate(reasons, 1):
        lines.append(f"  {i}. {r}")

    if is_duplicate:
        lines.append(f"\n⚡ Previously analyzed on {result['created_at']}")

    return "\n".join(lines)


def process_message(cl: IGClient, thread_id: str, message) -> None:
    text = message.text or ""
    url = utils.extract_first_url(text)

    if not url:
        if hasattr(message, "clip") and message.clip:
            url = f"https://www.instagram.com/reel/{message.clip.pk}/"
        elif hasattr(message, "media_share") and message.media_share:
            url = f"https://www.instagram.com/p/{message.media_share.code}/"

    if not url:
        logger.debug(f"No URL found in message: {text[:50]}")
        return

    logger.info(f"Processing URL: {url}")
    normalized = utils.normalize_url(url)
    fingerprint = utils.compute_fingerprint(normalized)

    # Check for duplicate
    existing = db.find_by_fingerprint(fingerprint)
    if existing:
        logger.info(f"Duplicate found for fingerprint {fingerprint[:12]}...")
        reply = format_reply(existing, is_duplicate=True)
        cl.direct_send(reply, thread_ids=[thread_id])
        return

    # New analysis
    logger.info(f"Analyzing URL with Gemini...")
    result = analyzer.analyze_url(url)

    record = db.insert_analysis(
        source_url=url,
        fingerprint=fingerprint,
        verdict=result["verdict"],
        confidence=result["confidence"],
        reasons=result["reasons"],
    )

    reply = format_reply(record, is_duplicate=False)
    cl.direct_send(reply, thread_ids=[thread_id])
    logger.info(f"Replied with verdict: {result['verdict']}")


def poll_inbox(cl: IGClient):
    """Poll DM inbox for new messages with URLs."""
    my_pk = cl.user_id
    logger.info("Fetching inbox threads...")
    threads = cl.direct_threads(amount=20, selected_filter="unread")

    for thread in threads:
        if not thread.messages:
            continue

        msg = thread.messages[0]  # newest message

        if str(msg.user_id) == str(my_pk):
            continue

        try:
            process_message(cl, thread.id, msg)
        except Exception as e:
            logger.error(f"Error processing thread {thread.id}: {e}", exc_info=True)


def run_polling_loop():
    """Main polling loop."""
    db.init_db()
    cl = create_client()
    logger.info(f"Bot started. Polling every {POLL_INTERVAL}s...")

    while True:
        try:
            poll_inbox(cl)
        except RATE_LIMIT_ERRORS as e:
            logger.warning(f"Rate limit hit: {e}")
            cl = rotate_on_rate_limit(cl)
        except Exception as e:
            logger.error(f"Polling error: {e}", exc_info=True)
            try:
                cl = create_client()
            except Exception:
                logger.error("Re-login failed, will retry next cycle")

        time.sleep(POLL_INTERVAL)
