"""
Tolkar om HAE-payloads med pass av en sport som saknade alias när de kom in.

En payload tolkas bara en gång: parse_hae läser bara raw_import med status
'received'. Ett nytt alias ger därför inget pass för det som redan hoppats över.

Urvalet görs ur databasen när skriptet körs, inte ur en id-lista: varje
HAE-payload med ett pass av något av namnen där passet inte finns, varken med
samma id i workout (från HAE eller som FIT-passets UUID) eller som matchning i
workout_match. En payload som kommit in efter att någon räknat kommer därför
med, och körs skriptet igen hittar det inget.

Skriver till traning.db, så det körs under pipelinens lås:

    flock -w 120 /tmp/traning.lock .venv/bin/python omtolka_hae.py Pilates "Inomhus Cykling"
"""
import json
import sqlite3
import sys

import parse_hae


def saknade(conn, namn):
    """{raw_import.id: [HAE-id, ...]} för payloads med pass av namnen som saknar pass."""
    src_id = conn.execute(
        "SELECT id FROM source WHERE code='apple_health_hae'").fetchone()[0]
    # Förfilter i SQL, så att inte varje payload på upp till 16 MB läses in. Båda
    # skrivsätten, ifall HAE skickar å, ä och ö som \\u-escape. Exakt jämförelse
    # på name görs sedan på det tolkade objektet.
    nalar = sorted({json.dumps(n, ensure_ascii=a) for n in namn for a in (False, True)})
    villkor = " OR ".join("instr(payload, ?) > 0" for _ in nalar)
    ut = {}
    for rid, payload in conn.execute(
            f"""SELECT id, payload FROM raw_import
                WHERE source_id=? AND status='parsed' AND ({villkor}) ORDER BY id""",
            (src_id, *nalar)):
        try:
            workouts = json.loads(payload)["data"]["workouts"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        for w in workouts:
            if w.get("name") not in namn:
                continue
            finns = conn.execute(
                """SELECT 1 FROM workout WHERE external_id=?
                   UNION ALL
                   SELECT 1 FROM workout_match WHERE source_id=? AND external_id=?""",
                (w["id"], src_id, w["id"])).fetchone()
            if not finns:
                ut.setdefault(rid, []).append(w["id"])
    return ut


def main(namn):
    if not namn:
        sys.exit("Ange HAE-namnen, t.ex.: omtolka_hae.py Pilates")
    conn = sqlite3.connect(parse_hae.DB_PATH, timeout=30)
    try:
        alias = {r[0] for r in conn.execute(
            """SELECT a.external_code FROM sport_alias a JOIN source s ON s.id = a.source_id
               WHERE s.code = 'apple_health_hae'""")}
        utan = [n for n in namn if n not in alias]
        if utan:
            # Utan alias hoppas passet över igen, och payloaden är förbrukad på nytt.
            sys.exit("Saknar alias i sport_alias, kör migrationen först: " + ", ".join(utan))
        urval = saknade(conn, set(namn))
        for rid, ids in urval.items():
            print(f"raw_import {rid}: {', '.join(ids)}")
        if not urval:
            print("Inget att tolka om: alla pass med de namnen finns.")
            return []
        conn.executemany("UPDATE raw_import SET status='received' WHERE id=?",
                         [(rid,) for rid in urval])
        conn.commit()
    finally:
        conn.close()
    parse_hae.main()
    return sorted(urval)


if __name__ == "__main__":
    main(sys.argv[1:])
