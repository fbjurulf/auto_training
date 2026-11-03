"""Spelar upp tests/data mot en testdatabas i produktionens ordning.

Varje händelse i replay.json är antingen en HAE-payload som kommer in via
ingest och tolkas av parse_hae, eller en FIT-fil som dyker upp i fit/ och
tolkas av parse_fit. Ordningen spelar roll: den avgör vilken källa som
skapar passet och vilken som skriver sist.
"""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from tests.fixtures import build_db

DATA_DIR = Path(__file__).resolve().parent / "data"


def replay():
    """Bygg databasen och spela upp allt. Ger (conn, tmpdir) - städa tmpdir."""
    tmp = Path(tempfile.mkdtemp(prefix="traning-test-"))
    os.environ["TRANING_DB"] = str(tmp / "traning.db")
    os.environ["TRANING_FIT_DIR"] = str(tmp / "fit")
    for var in ("TRANING_PARSE_LOG", "TRANING_FIT_LOG", "TRANING_WEEKLY_LOG"):
        os.environ[var] = str(tmp / "logs" / f"{var.lower()}.log")
    os.environ["TRANING_REPORT_DIR"] = str(tmp / "rapporter")
    (tmp / "fit").mkdir()
    build_db(os.environ["TRANING_DB"]).close()

    import parse_fit
    import parse_hae
    # Modulerna läser miljön vid import - sätt om ifall de redan importerats.
    parse_hae.DB_PATH = parse_fit.DB_PATH = os.environ["TRANING_DB"]
    parse_fit.FIT_DIR = tmp / "fit"

    events = json.loads((DATA_DIR / "replay.json").read_text(encoding="utf-8"))
    for event in events:
        if "hae" in event:
            _ingest_hae(event)
            parse_hae.main()
        else:
            shutil.copy(DATA_DIR / event["fit"], tmp / event["fit"])
            parse_fit.main()

    conn = sqlite3.connect(os.environ["TRANING_DB"])
    conn.row_factory = sqlite3.Row
    return conn, tmp


def _ingest_hae(event):
    workouts = [json.loads(gzip.decompress((DATA_DIR / f).read_bytes()))
                for f in event["hae"]]
    payload = json.dumps({"data": {"workouts": workouts}}, ensure_ascii=False)
    conn = sqlite3.connect(os.environ["TRANING_DB"])
    conn.execute(
        """INSERT INTO raw_import
           (source_id, received_at, payload, payload_sha256, byte_size, workout_count)
           VALUES ((SELECT id FROM source WHERE code = 'apple_health_hae'), ?, ?, ?, ?, ?)""",
        (event["at"], payload, hashlib.sha256(payload.encode()).hexdigest(),
         len(payload.encode()), len(workouts)))
    conn.commit()
    conn.close()
