"""
Mottagare för Health Auto Export (och framtida källor).

Enda ansvar: ta emot en payload, verifiera token, skriv den rå till
raw_import och svara snabbt. Ingen tolkning sker här - det görs av
parse-steget, som kan köras om mot samma rådata.
"""

import hashlib
import json
import logging
import os
import subprocess
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("TRANING_DB", str(BASE_DIR / "traning.db"))
LOG_PATH = os.environ.get("TRANING_LOG", str(BASE_DIR / "logs" / "ingest.log"))
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")
MAX_BYTES = int(os.environ.get("INGEST_MAX_BYTES", 50 * 1024 * 1024))

Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("ingest")
logger.setLevel(logging.INFO)
_handler = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=5)
_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(message)s")
)
logger.addHandler(_handler)

app = FastAPI(title="Traningsdataarkiv - ingest", docs_url=None, redoc_url=None)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def run_pipeline():
    try:
        subprocess.run(["/home/fredrik/traning/pipeline.sh"],
                       timeout=600, capture_output=True, check=True)
    except Exception as exc:
        logger.error("pipeline.sh failade: %s", exc)

def source_id(conn, code: str) -> int:
    row = conn.execute("SELECT id FROM source WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail=f"Okänd källa: {code}")
    return row["id"]


def check_token(authorization: str | None, x_token: str | None) -> None:
    if not INGEST_TOKEN:
        raise HTTPException(status_code=500, detail="INGEST_TOKEN saknas i miljön")
    supplied = None
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    elif x_token:
        supplied = x_token.strip()
    if supplied != INGEST_TOKEN:
        raise HTTPException(status_code=401, detail="Ogiltig token")


def count_workouts(payload: dict) -> int:
    try:
        return len(payload["data"]["workouts"])
    except (KeyError, TypeError):
        return 0


@app.get("/health")
def health():
    """Enkel livscheck - används av healthchecks och av dig själv."""
    with db() as conn:
        last = conn.execute(
            "SELECT received_at FROM raw_import ORDER BY id DESC LIMIT 1"
        ).fetchone()
        total = conn.execute("SELECT COUNT(*) AS n FROM workout").fetchone()["n"]
    return {
        "status": "ok",
        "senaste_import": last["received_at"] if last else None,
        "antal_pass": total,
    }


@app.post("/ingest/{source_code}")
async def ingest(
    source_code: str,
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
    x_ingest_token: str | None = Header(default=None),
):
    check_token(authorization, x_ingest_token)

    body = await request.body()
    if len(body) > MAX_BYTES:
        logger.error("Payload för stor: %s bytes från %s", len(body), source_code)
        raise HTTPException(status_code=413, detail="Payload för stor")

    digest = hashlib.sha256(body).hexdigest()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        with db() as conn:
            conn.execute(
                """INSERT INTO raw_import
                   (source_id, received_at, payload, payload_sha256,
                    byte_size, workout_count, status, error)
                   VALUES (?,?,?,?,?,?,'failed',?)""",
                (
                    source_id(conn, source_code),
                    utc_now(),
                    body.decode("utf-8", errors="replace")[:1_000_000],
                    digest,
                    len(body),
                    0,
                    f"JSONDecodeError: {exc}",
                ),
            )
        logger.error("Ogiltig JSON från %s: %s", source_code, exc)
        raise HTTPException(status_code=400, detail="Ogiltig JSON")

    n_workouts = count_workouts(payload)

    with db() as conn:
        sid = source_id(conn, source_code)
        dup = conn.execute(
            """SELECT id FROM raw_import
               WHERE source_id = ? AND payload_sha256 = ?
               ORDER BY id DESC LIMIT 1""",
            (sid, digest),
        ).fetchone()

        cur = conn.execute(
            """INSERT INTO raw_import
               (source_id, received_at, payload, payload_sha256,
                byte_size, workout_count, status)
               VALUES (?,?,?,?,?,?,?)""",
            (
                sid,
                utc_now(),
                body.decode("utf-8"),
                digest,
                len(body),
                n_workouts,
                "ignored" if dup else "received",
            ),
        )
        raw_id = cur.lastrowid

    if dup:
        logger.info(
            "Identisk payload igen (raw_import %s, samma som %s), %s pass",
            raw_id, dup["id"], n_workouts,
        )
    else:
        logger.info(
            "Tog emot %s pass från %s (raw_import %s, %s bytes)",
            n_workouts, source_code, raw_id, len(body),
        )
    if not dup:
        background_tasks.add_task(run_pipeline)
    return {
        "raw_import_id": raw_id,
        "workouts_i_payload": n_workouts,
        "dubblett": bool(dup),
    }
