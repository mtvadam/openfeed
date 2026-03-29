import json
import logging
import os
from google import genai

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

PROMPT_TEMPLATE = """You are a media-authenticity analyst. Given the URL below, classify the content it points to.

URL: {url}

Respond with ONLY valid JSON matching this schema — no markdown, no explanation:
{{
  "verdict": "<Real | AI Generated | Manipulated | Inconclusive>",
  "confidence": <integer 0-100>,
  "reasons": ["<reason 1>", "<reason 2>", "<reason 3>"]
}}

Rules:
- verdict must be exactly one of: Real, AI Generated, Manipulated, Inconclusive
- confidence is an integer from 0 to 100
- reasons is an array of exactly 3 short strings
- Do NOT wrap the JSON in code fences
"""

RICH_PROMPT_TEMPLATE = """You are a media-authenticity analyst. Analyze the following TikTok post and classify its content.

URL: {url}
Author: {author}
Description: {description}
Video URL: {video_url}

Respond with ONLY valid JSON matching this schema — no markdown, no explanation:
{{
  "verdict": "<Real | AI Generated | Manipulated | Inconclusive>",
  "confidence": <integer 0-100>,
  "reasons": ["<reason 1>", "<reason 2>", "<reason 3>"]
}}

Rules:
- verdict must be exactly one of: Real, AI Generated, Manipulated, Inconclusive
- confidence is an integer from 0 to 100
- reasons is an array of exactly 3 short strings
- Do NOT wrap the JSON in code fences
"""


def fetch_tiktok_details(url: str) -> dict | None:
    """Fetch TikTok post metadata using pyktok. Returns dict with author, description, video_url."""
    try:
        import pyktok as pyk
        data = pyk.alt_get_tiktok_json(url)
        if not data or "__DEFAULT_SCOPE__" not in data:
            return None

        detail = data["__DEFAULT_SCOPE__"].get("webapp.video-detail", {})
        item = detail.get("itemInfo", {}).get("itemStruct", {})
        if not item:
            return None

        result = {
            "author": item.get("author", {}).get("uniqueId", "unknown"),
            "description": item.get("desc", ""),
            "video_url": item.get("video", {}).get("playAddr", ""),
        }
        logger.info(f"TikTok details — author: @{result['author']}, desc: {result['description'][:80]}")
        return result
    except Exception as e:
        logger.warning(f"Could not fetch TikTok details: {e}")
        return None


def analyze_url(url: str, metadata: dict | None = None) -> dict:
    """Call Gemini to classify the URL content. Returns dict with verdict, confidence, reasons.

    If metadata is provided (author, description, video_url), it is used directly
    instead of re-fetching via pyktok.
    """
    client = genai.Client(api_key=GEMINI_API_KEY)

    # Use provided metadata, fall back to pyktok fetch, fall back to basic prompt
    prompt = PROMPT_TEMPLATE.format(url=url)
    details = metadata if metadata and metadata.get("author") else None
    if not details and "tiktok.com" in url:
        details = fetch_tiktok_details(url)
    if details:
        prompt = RICH_PROMPT_TEMPLATE.format(
            url=url,
            author=details.get("author", "unknown"),
            description=details.get("description", ""),
            video_url=details.get("video_url", ""),
        )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    text = response.text.strip()

    # Strip markdown code fences if Gemini wraps anyway
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    result = json.loads(text)

    # Validate / clamp
    valid_verdicts = {"Real", "AI Generated", "Manipulated", "Inconclusive"}
    if result.get("verdict") not in valid_verdicts:
        result["verdict"] = "Inconclusive"
    result["confidence"] = max(0, min(100, int(result.get("confidence", 50))))
    reasons = result.get("reasons", [])
    if not isinstance(reasons, list) or len(reasons) != 3:
        reasons = (reasons + ["N/A"] * 3)[:3]
    result["reasons"] = reasons

    return result
