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

# XPath locators (based on TikTok's data-e2e attributes)
LOCATORS = {
    "CHAT_LIST_ITEM": '//div[@data-e2e="chat-list-item"]',
    "CHAT_ITEM": '//div[@data-e2e="chat-item"]',
    "CHAT_UNIQUEID": '//p[@data-e2e="chat-uniqueid"]',
    "DM_INPUT": '//div[@aria-label="Send a message..." and @role="textbox"]',
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
    """Type and send a message in the currently open chat."""
    try:
        input_el = _wait(driver, LOCATORS["DM_INPUT"], 10)

        # Clear any existing text
        existing = input_el.text
        if existing and existing not in ["Send a message...", "", None]:
            input_el.send_keys(Keys.CONTROL + "a")
            input_el.send_keys(Keys.DELETE)

        _paste_text(driver, LOCATORS["DM_INPUT"], msg)
        time.sleep(0.5)

        # Verify text was entered, then send
        if input_el.text:
            _wait_and_click(driver, LOCATORS["DM_SEND"], 5)
            time.sleep(1)
            logger.info("Reply sent")
            return True

        logger.warning("Message text not entered correctly, retrying...")
        return False
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return False


def _get_unreplied_video_urls(driver) -> list[str]:
    """Find all shared TikTok video URLs in the chat that we haven't replied to yet.
    A video is 'replied' if an 'OpenFeed Analysis' message follows it."""
    video_urls = []
    try:
        items = driver.find_elements(By.XPATH, LOCATORS["CHAT_ITEM"])

        # Walk through all chat items and track video URLs and our replies
        pending_urls = []
        for item in items:
            text = item.text.strip()

            # Check if this is our reply (contains our signature)
            if "OpenFeed Analysis" in text:
                # We already replied to whatever came before — clear pending
                pending_urls.clear()
                continue

            # Check if this item contains a shared TikTok video link
            links = item.find_elements(By.TAG_NAME, "a")
            for link in links:
                href = link.get_attribute("href") or ""
                if "tiktok.com" in href and "/video/" in href:
                    pending_urls.append(href)
                    break

        # Whatever's left in pending_urls has no reply yet
        video_urls = pending_urls
        logger.info(f"Found {len(video_urls)} unreplied video(s) in chat")
    except Exception as e:
        logger.debug(f"Error scanning chat: {e}")
    return video_urls


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------

def format_reply(result: dict, is_duplicate: bool) -> str:
    reasons = (
        json.loads(result["reasons_json"])
        if isinstance(result["reasons_json"], str)
        else result["reasons_json"]
    )
    lines = [
        "OpenFeed Analysis",
        "",
        f"Verdict: {result['verdict']}",
        f"Confidence: {result['confidence']}%",
        "",
        "Reasons:",
    ]
    for i, r in enumerate(reasons, 1):
        lines.append(f"  {i}. {r}")

    if is_duplicate:
        lines.append(f"\nPreviously analyzed on {result['created_at']}")

    return "\n".join(lines)


def _process_message(driver, url: str) -> None:
    logger.info(f"Processing URL: {url}")
    normalized = utils.normalize_url(url)
    fingerprint = utils.compute_fingerprint(normalized)

    existing = db.find_by_fingerprint(fingerprint)
    if existing:
        logger.info(f"Duplicate found for fingerprint {fingerprint[:12]}...")
        reply = format_reply(existing, is_duplicate=True)
        _send_in_chat(driver, reply)
        return

    logger.info("Analyzing URL with Gemini...")
    result = analyzer.analyze_url(url)

    record = db.insert_analysis(
        source_url=url,
        fingerprint=fingerprint,
        verdict=result["verdict"],
        confidence=result["confidence"],
        reasons=result["reasons"],
    )

    reply = format_reply(record, is_duplicate=False)
    _send_in_chat(driver, reply)
    logger.info(f"Replied with verdict: {result['verdict']}")


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
            logger.info(f"Clicking {label} item {i}...")
            item.click()
            time.sleep(3)

            # Accept message request if needed
            if label == "request":
                if not _try_accept_request(driver):
                    logger.debug(f"No accept prompt for {label} {i}, skipping")
                    continue

            # Wait for chat to load
            if not _is_element_present(driver, LOCATORS["DM_INPUT"], 8):
                logger.info(f"Chat input not found for {label} {i}")
                continue

            # Find unreplied video URLs
            unreplied = _get_unreplied_video_urls(driver)
            if not unreplied:
                logger.info(f"No unreplied videos in {label} {i}")
                continue

            # Process each unreplied video
            for url in unreplied:
                url_key = url[:200]
                if url_key in _processed:
                    continue
                _processed.add(url_key)
                _process_message(driver, url)

        except StaleElementReferenceException:
            logger.debug("Stale element, refreshing next cycle")
            break
        except Exception as e:
            logger.error(f"Error processing {label} {i}: {e}", exc_info=True)


def poll_inbox(driver) -> None:
    """Check for new unread DMs, including message requests."""
    driver.get(MESSAGES_URL)
    time.sleep(3)

    if "/login" in driver.current_url:
        logger.warning("Session expired — re-logging in")
        _login(driver)
        return

    # --- Check message requests first ---
    if _is_element_present(driver, LOCATORS["MSG_REQUESTS"], 3):
        logger.info("Found message requests — clicking into it...")
        try:
            el = _wait(driver, LOCATORS["MSG_REQUESTS"], 3)
            # Use JS click — the element is often not interactable via normal click
            driver.execute_script("arguments[0].click();", el)
            time.sleep(3)
        except Exception as e:
            logger.debug(f"Could not click message requests: {e}")
            driver.get(MESSAGES_URL)
            time.sleep(3)

        # Look for request items (try multiple selectors)
        request_items = driver.find_elements(By.XPATH, LOCATORS["MSG_REQUEST_ITEM"])
        if not request_items:
            # Fallback: any clickable chat-like items in the request list
            request_items = driver.find_elements(By.XPATH, LOCATORS["CHAT_LIST_ITEM"])

        if request_items:
            logger.info(f"Found {len(request_items)} message requests")
            _process_chat_list(driver, request_items, label="request")

        # Navigate back to main messages
        driver.get(MESSAGES_URL)
        time.sleep(3)

    # --- Regular chat list ---
    if _is_element_present(driver, LOCATORS["CHAT_LIST_ITEM"], 5):
        chat_items = driver.find_elements(By.XPATH, LOCATORS["CHAT_LIST_ITEM"])
        logger.info(f"Found {len(chat_items)} conversations")
        _process_chat_list(driver, chat_items, label="chat")
    else:
        logger.info("No conversations in chat list")

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
