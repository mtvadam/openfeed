import json
import os
import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS analyses (
    id BIGSERIAL PRIMARY KEY,
    source_url TEXT NOT NULL,
    fingerprint TEXT UNIQUE NOT NULL,
    verdict TEXT NOT NULL,
    confidence INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def get_connection():
    from urllib.parse import urlparse, unquote
    parsed = urlparse(DATABASE_URL)
    return psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        dbname=parsed.path.lstrip("/"),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def init_db():
    conn = get_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE)
    conn.close()
    logger.info("Database initialized")


def find_by_fingerprint(fingerprint: str) -> dict | None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM analyses WHERE fingerprint = %s LIMIT 1", (fingerprint,))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def insert_analysis(
    source_url: str,
    fingerprint: str,
    verdict: str,
    confidence: int,
    reasons: list[str],
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    reasons_json = json.dumps(reasons)
    conn = get_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO analyses (source_url, fingerprint, verdict, confidence, reasons_json, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (source_url, fingerprint, verdict, confidence, reasons_json, now),
            )
            row = cur.fetchone()
    conn.close()
    return dict(row)


def get_all_analyses() -> list[dict]:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM analyses ORDER BY created_at DESC")
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]
