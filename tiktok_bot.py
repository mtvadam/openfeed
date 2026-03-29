import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)

import analyzer
import db
import utils

logger = logging.getLogger(__name__)

TT_USERNAME = os.getenv("TT_USERNAME", "")
TT_PASSWORD = os.getenv("TT_PASSWORD", "")
TT_PROFILE = os.getenv("TT_PROFILE", "openfeed")
TT_HEADLESS = os.getenv("TT_HEADLESS", "false").lower() == "true"
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))

MESSAGES_URL = "https://www.tiktok.com/messages?lang=en"

# Track messages we've already replied to
_processed: set[str] = set()
# Track the last chat name we checked to avoid re-clicking
_last_checked_chat: str | None = None

# XPath locators (based on TikTok's data-e2e attributes)
LOCATORS = {
    "CHAT_LIST_ITEM": '//div[@data-e2e="chat-list-item"]',
    "CHAT_ITEM": '//div[@data-e2e="chat-item"]',
    "CHAT_UNIQUEID": '//p[@data-e2e="chat-uniqueid"]',
    "DM_INPUT": '//div[@role="textbox"] | //div[contains(@aria-label,"Send a message")] | //div[contains(@class,"message-input")]',
    "DM_SEND": '//*[@role="button" and @data-e2e="message-send"]',
    "DM_WARN": "//div[@data-e2e='dm-warning']//*[@xmlns='http://www.w3.org/2000/svg']",
    "DM_WARN_TOO_FAST": "//div[text()='You are sending messages too fast. Take a rest.']",
    "MSG_REQUESTS": '//*[contains(text(),"You have") and contains(text(),"request")]',
    "MSG_REQUEST_ITEM": '//div[@data-e2e="message-request-item"]',
    "MSG_REQUEST_ACCEPT": '//*[text()="Accept"]',
    "LOGIN_EMAIL": './/input[@placeholder="Email or username"]',
    "LOGIN_PASSWORD": './/input[@placeholder="Password"]',
    "LOGIN_SUBMIT": ".//button[@type='submit' and text()='Log in']",
    "CAPTCHA": './/div[@role="dialog"]//div//div//a//span[text()="Report a problem"]',
}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _create_driver() -> uc.Chrome:
    options = uc.ChromeOptions()

    profile_dir = os.path.join(os.getcwd(), "profiles", TT_PROFILE)
    os.makedirs(profile_dir, exist_ok=True)
    options.add_argument(f"--user-data-dir={profile_dir}")

    options.add_argument("--lang=en_US")
    options.add_argument("--mute-audio")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-site-isolation-trials")

    if TT_HEADLESS:
        options.add_argument("--headless=new")

    # Enable performance logging to intercept network requests
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    # Auto-detect installed Chrome version
    chrome_version = None
    try:
        import subprocess
        out = subprocess.check_output(
            ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--version"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        chrome_version = int(out.split()[-1].split(".")[0])
        logger.info(f"Detected Chrome version: {chrome_version}")
    except Exception:
        pass

    driver = uc.Chrome(options=options, version_main=chrome_version)
    return driver


# ---------------------------------------------------------------------------
# Helpers (modeled after tiktok_dm reference repo)
# ---------------------------------------------------------------------------

def _is_element_present(driver, xpath, timeout=0) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        return True
    except Exception:
        return False


def _wait(driver, xpath, timeout=10):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.XPATH, xpath))
    )


def _wait_and_click(driver, xpath, timeout=10):
    el = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.XPATH, xpath))
    )
    el.click()
    return el


def _paste_text(driver, xpath, text):
    action = ActionChains(driver)
    el = _wait(driver, xpath, 10)
    action.move_to_element(el)
    action.click()
    action.send_keys(text)
    action.perform()


def _put_consent_cookie(driver):
    """Inject cookie-consent to skip the consent banner."""
    future_date = datetime.now() + timedelta(days=365 * 3)
    cookie = {
        "name": "cookie-consent",
        "value": '{"ga":false,"af":false,"fbp":false,"lip":false,"bing":false,"ttads":false,"reddit":false,"hubspot":false,"version":"v10"}',
        "domain": ".tiktok.com",
        "expiry": int(future_date.timestamp()),
        "httponly": False,
        "secure": True,
        "samesite": "None",
    }
    try:
        driver.add_cookie(cookie)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Login (follows the reference repo pattern)
# ---------------------------------------------------------------------------

def _login(driver: uc.Chrome) -> bool:
    logger.info("Navigating to messages to check login status...")
    driver.get(MESSAGES_URL)
    time.sleep(3)

    # Already logged in?
    if "login" not in driver.current_url:
        if "/business-suite/messages" in driver.current_url:
            logger.info("Business account detected")
        logger.info("Already logged in via Chrome profile")
        return True

    # No credentials — need manual login
    if not TT_USERNAME or not TT_PASSWORD:
        logger.info("=" * 50)
        logger.info("MANUAL LOGIN REQUIRED")
        logger.info("Log in to TikTok in the browser window.")
        logger.info("Bot will continue once you reach the For You page.")
        logger.info("=" * 50)

        # Wait up to 5 min for user to log in
        for _ in range(300):
            time.sleep(1)
            try:
                if "/login" not in driver.current_url and "tiktok.com" in driver.current_url:
                    logger.info("Login detected!")
                    driver.get(MESSAGES_URL)
                    time.sleep(3)
                    return True
            except Exception:
                pass

        logger.error("Login timed out after 5 minutes")
        return False

    # Automated login with credentials
    logger.info("Logging in with credentials...")
    _put_consent_cookie(driver)
    time.sleep(1)
    driver.get("https://www.tiktok.com/login/phone-or-email/email")
    time.sleep(2)

    if not _is_element_present(driver, LOCATORS["LOGIN_EMAIL"], 10):
        logger.error("Could not find login page")
        return False

    time.sleep(1)
    _paste_text(driver, LOCATORS["LOGIN_EMAIL"], TT_USERNAME)
    time.sleep(1)
    _paste_text(driver, LOCATORS["LOGIN_PASSWORD"], TT_PASSWORD)
    time.sleep(1)
    _wait_and_click(driver, LOCATORS["LOGIN_SUBMIT"], 5)

    # Wait for login to complete (captcha, 2fa, or success)
    for _ in range(120):
        time.sleep(1)
        if _is_element_present(driver, LOCATORS["CAPTCHA"], 0):
            logger.warning("CAPTCHA detected — please solve it manually in the browser")
            continue
        if "/login" not in driver.current_url and "tiktok.com" in driver.current_url:
            logger.info("Login successful")
            driver.get(MESSAGES_URL)
            time.sleep(3)
            return True

    logger.error("Login failed — timed out waiting for redirect")
    return False


# ---------------------------------------------------------------------------
# DM interaction
# ---------------------------------------------------------------------------

def _send_in_chat(driver, msg: str) -> bool:
    """Type and send a message in the currently open chat using clipboard paste."""
    try:
        import subprocess

        input_el = _wait(driver, LOCATORS["DM_INPUT"], 10)
        input_el.click()
        time.sleep(0.3)

        # Copy message to clipboard (macOS)
        process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        process.communicate(msg.encode("utf-8"))

        # Paste from clipboard (Cmd+V on Mac)
        action = ActionChains(driver)
        action.key_down(Keys.COMMAND).send_keys("v").key_up(Keys.COMMAND)
        action.perform()
        time.sleep(0.5)

        # Click send button
        _wait_and_click(driver, LOCATORS["DM_SEND"], 5)
        time.sleep(1)
        logger.info("Reply sent")
        return True
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return False


def _get_video_items_from_network(driver) -> list[dict]:
    """Extract video metadata from intercepted im/item_detail API responses."""
    videos = []
    try:
        logs = driver.get_log("performance")
        logger.info(f"Performance log entries: {len(logs)}")
    except Exception as e:
        logger.debug(f"Could not get performance logs: {e}")
        return videos

    im_detail_count = 0
    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
            if msg["method"] != "Network.responseReceived":
                continue
            resp_url = msg["params"]["response"]["url"]
            if "/api/im/item_detail/" not in resp_url:
                continue
            im_detail_count += 1
            logger.info(f"Found im/item_detail response #{im_detail_count}")

            request_id = msg["params"]["requestId"]
            try:
                body = driver.execute_cdp_cmd(
                    "Network.getResponseBody", {"requestId": request_id}
                )
                data = json.loads(body["body"])
                item = data.get("itemInfo", {}).get("itemStruct", {})
                if not item:
                    continue

                author = item.get("author", {}).get("uniqueId", "")
                item_id = item.get("id", "")
                if not author or not item_id:
                    continue

                desc = item.get("desc", "")
                video_play_url = item.get("video", {}).get("playAddr", "")
                sticker_texts = []
                for s in item.get("stickersOnItem", []):
                    sticker_texts.extend(s.get("stickerText", []))

                videos.append({
                    "url": f"https://www.tiktok.com/@{author}/video/{item_id}",
                    "item_id": item_id,
                    "author": author,
                    "description": desc + (" | " + " | ".join(sticker_texts) if sticker_texts else ""),
                    "video_url": video_play_url,
                })
                logger.info(f"Intercepted video: @{author}/video/{item_id}")
            except Exception as e:
                logger.debug(f"Could not get response body for request {request_id}: {e}")
        except Exception:
            continue

    return videos


def _get_unreplied_videos(driver) -> list[dict]:
    """Find shared TikTok videos in the chat that we haven't replied to yet.
    Uses network interception to get real video URLs from im/item_detail API."""
    # Get videos from intercepted network requests
    videos = _get_video_items_from_network(driver)
    if not videos:
        logger.info("No video items found in network logs")
        return []

    # Count how many "OpenFeed Analysis" replies are already in the chat
    reply_count = 0
    try:
        items = driver.find_elements(By.XPATH, LOCATORS["CHAT_ITEM"])
        for item in items:
            try:
                text = item.text.strip()
                if "OpenFeed Analysis" in text:
                    reply_count += 1
            except StaleElementReferenceException:
                continue
    except Exception as e:
        logger.debug(f"Error counting replies: {e}")

    # The unreplied videos are the ones after our existing replies
    # Videos appear in chronological order; replies follow them
    unreplied = videos[reply_count:]
    logger.info(
        f"Found {len(videos)} video(s), {reply_count} already replied, "
        f"{len(unreplied)} unreplied"
    )
    return unreplied


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------

def format_reply(result: dict) -> str:
    reasons = result.get("reasons", [])
    if isinstance(reasons, str):
        reasons = json.loads(reasons)
    reasons_str = " | ".join(reasons[:3])
    return f"OpenFeed: {result['verdict']} ({result['confidence']}%) - {reasons_str}"


def _analyze_video(video: dict) -> str | None:
    """Analyze a video and return the reply text. Returns None if already analyzed."""
    url = video["url"]
    logger.info(f"Processing URL: {url}")
    normalized = utils.normalize_url(url)
    fingerprint = utils.compute_fingerprint(normalized)

    existing = db.find_by_fingerprint(fingerprint)
    if existing:
        logger.info(f"Already analyzed {fingerprint[:12]}, skipping")
        return None

    logger.info("Analyzing with Gemini...")
    metadata = {
        "author": video.get("author", ""),
        "description": video.get("description", ""),
        "video_url": video.get("video_url", ""),
    }
    result = analyzer.analyze_url(url, metadata=metadata)

    db.insert_analysis(
        source_url=url,
        fingerprint=fingerprint,
        verdict=result["verdict"],
        confidence=result["confidence"],
        reasons=result["reasons"],
    )

    reply = format_reply(result, is_duplicate=False)
    logger.info(f"Verdict: {result['verdict']}")
    return reply


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

def _try_accept_request(driver) -> bool:
    """Try to accept a message request if the prompt is showing."""
    for accept_xpath in [
        '//*[text()="Accept"]',
        '//div[text()="Accept"]',
        '//span[text()="Accept"]',
    ]:
        if _is_element_present(driver, accept_xpath, 1):
            try:
                el = _wait(driver, accept_xpath, 3)
                driver.execute_script("arguments[0].click();", el)
                logger.info("Message request accepted!")
                time.sleep(3)
                return True
            except Exception:
                continue
    return False


def _process_chat_list(driver, items, label="chat") -> None:
    """Process a list of chat item elements."""
    for i, item in enumerate(items):
        try:
            # Drain stale performance logs
            try:
                driver.get_log("performance")
            except Exception:
                pass

            # Click the conversation
            logger.info(f"Opening {label} {i}...")
            item.click()
            time.sleep(3)

            # Verify we're still on messages page (not redirected)
            if "/messages" not in driver.current_url:
                logger.warning(f"Navigated away from messages, aborting")
                return

            # Accept message request if needed
            if label == "request":
                if not _try_accept_request(driver):
                    continue
                time.sleep(2)

            # Wait for DM input to confirm chat loaded
            _is_element_present(driver, LOCATORS["DM_INPUT"], 5)

            # Get videos from network interception (already filters out replied ones)
            unreplied = _get_unreplied_videos(driver)
            if not unreplied:
                logger.info(f"No new videos in {label} {i}")
                return

            # Analyze all unreplied videos, collect replies, send as ONE message
            replies = []
            for video in unreplied:
                if video["item_id"] in _processed:
                    continue
                _processed.add(video["item_id"])
                reply = _analyze_video(video)
                if reply:
                    replies.append(reply)

            if replies:
                combined = "\n\n".join(replies)
                _send_in_chat(driver, combined)
                logger.info(f"Sent {len(replies)} analysis result(s) in 1 message")

        except StaleElementReferenceException:
            return
        except Exception as e:
            logger.error(f"Error in {label} {i}: {e}", exc_info=True)
            return


def _ensure_on_messages(driver, force_refresh=False) -> bool:
    """Make sure we're on the messages page. Navigate there if not."""
    on_messages = "/messages" in driver.current_url and "/login" not in driver.current_url
    if force_refresh or not on_messages:
        driver.get(MESSAGES_URL)
        time.sleep(3)
    if "/login" in driver.current_url:
        logger.warning("Session expired")
        return False
    return True


def poll_inbox(driver) -> None:
    """Check for new unread DMs, including message requests."""
    if not _ensure_on_messages(driver):
        _login(driver)
        return

    # --- Check message requests (only if new ones since last check) ---
    # Skip requests entirely for now — focus on main chat list
    # Message requests don't contain shared videos typically

    # --- Always fresh-load messages page to bust API cache ---
    if not _ensure_on_messages(driver, force_refresh=True):
        return

    chat_items = driver.find_elements(By.XPATH, LOCATORS["CHAT_LIST_ITEM"])
    if not chat_items:
        return

    _process_chat_list(driver, chat_items[:1], label="chat")

    if len(_processed) > 5000:
        _processed.clear()


def run_polling_loop() -> None:
    """Main polling loop."""
    db.init_db()
    driver = _create_driver()

    try:
        if not _login(driver):
            logger.error("Could not log in — exiting bot")
            return

        logger.info(f"TikTok bot started. Polling every {POLL_INTERVAL}s...")

        while True:
            try:
                poll_inbox(driver)
            except Exception as e:
                logger.error(f"Polling error: {e}", exc_info=True)
                try:
                    driver.get(MESSAGES_URL)
                    time.sleep(3)
                except Exception:
                    logger.error("Recovery failed — restarting browser")
                    driver.quit()
                    driver = _create_driver()
                    _login(driver)

            time.sleep(POLL_INTERVAL)
    finally:
        driver.quit()
