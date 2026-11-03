"""Testdatabas byggd av produktionens schema, se sql/migrations/README.md."""
import sqlite3
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def build_db(path=":memory:"):
    """Bygg schema_base, seed_reference och migrationerna i nummerordning.

    Ange en filsökväg för kod som öppnar databasen själv via TRANING_DB.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON;")
    scripts = [SQL_DIR / "schema_base.sql", SQL_DIR / "seed_reference.sql"]
    scripts += sorted((SQL_DIR / "migrations").glob("[0-9]*.sql"))
    for script in scripts:
        conn.executescript(script.read_text(encoding="utf-8"))
    conn.commit()
    return conn
