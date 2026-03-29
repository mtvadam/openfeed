import json
import logging
import os
import threading

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from pydantic import BaseModel

import db
import analyzer
import utils
import tiktok_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="OpenFeed", version="0.1.0")
db.init_db()


# --- API Models ---

class AnalyzeRequest(BaseModel):
    url: str


class AnalysisResponse(BaseModel):
    source_url: str
    fingerprint: str
    verdict: str
    confidence: int
    reasons: list[str]
    is_duplicate: bool
    created_at: str


# --- Endpoints ---

@app.get("/health")
def health():
    return {"status": "ok", "service": "openfeed"}


@app.get("/history")
def history():
    rows = db.get_all_analyses()
    for r in rows:
        r["reasons"] = json.loads(r["reasons_json"])
        del r["reasons_json"]
    return rows


@app.post("/analyze-url", response_model=AnalysisResponse)
def analyze_url(req: AnalyzeRequest):
    normalized = utils.normalize_url(req.url)
    fingerprint = utils.compute_fingerprint(normalized)

    existing = db.find_by_fingerprint(fingerprint)
    if existing:
        return AnalysisResponse(
            source_url=existing["source_url"],
            fingerprint=existing["fingerprint"],
            verdict=existing["verdict"],
            confidence=existing["confidence"],
            reasons=json.loads(existing["reasons_json"]),
            is_duplicate=True,
            created_at=existing["created_at"],
        )

    result = analyzer.analyze_url(req.url)

    record = db.insert_analysis(
        source_url=req.url,
        fingerprint=fingerprint,
        verdict=result["verdict"],
        confidence=result["confidence"],
        reasons=result["reasons"],
    )

    return AnalysisResponse(
        source_url=req.url,
        fingerprint=fingerprint,
        verdict=result["verdict"],
        confidence=result["confidence"],
        reasons=result["reasons"],
        is_duplicate=False,
        created_at=record["created_at"],
    )


# --- Start DM polling in background thread ---

def start_bot():
    try:
        tiktok_bot.run_polling_loop()
    except Exception as e:
        logger.error(f"Bot thread crashed: {e}", exc_info=True)


@app.on_event("startup")
def on_startup():
    if os.getenv("TT_ENABLED", "true").lower() == "true":
        bot_thread = threading.Thread(target=start_bot, daemon=True)
        bot_thread.start()
        logger.info("TikTok bot thread started")
    else:
        logger.warning("TT_ENABLED is false — bot disabled, API-only mode")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "3000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
