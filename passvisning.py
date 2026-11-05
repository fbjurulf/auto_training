"""
Allt som finns lagrat om ett pass, för när ett tal ser fel ut i en rapport.

Tabellerna hittas i schemat, via främmande nycklar mot workout, och raderna läses
med SELECT *. Det finns ingen fältlista som kan glömma något: en ny kolumn eller
tabell syns direkt. Saknade rader och tomma fält skrivs ut, de är ofta själva svaret
(pass 76 har ingen rad i workout_swim, cykelpassen ingen i workout_bike). Läser från
workout, inte från vyerna, så att dolda pass och dubbletter också går att se.
"""
import fitstatus


def _varde(v):
    if v is None:
        return "NULL"
    return str(v).replace("\r", "\\r").replace("\n", "\\n")


def _rad(r):
    return " ".join(f"{k}={_varde(r[k])}" for k in r.keys())


def _tabeller(conn):
    """Alla (tabell, kolumn) med en främmande nyckel mot workout."""
    ut = []
    for (tabell,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        for fk in conn.execute(f'PRAGMA foreign_key_list("{tabell}")').fetchall():
            if fk["table"] == "workout":
                ut.append((tabell, fk["from"]))
    return ut


def _segment(rader):
    """Aggregatsegmenten i ordning, med underintervallen indragna under sitt segment."""
    i_ordning = sorted(rader, key=lambda r: r["seq"])
    ids = {r["id"] for r in rader}
    barn = {}
    for r in i_ordning:
        barn.setdefault(r["parent_id"], []).append(r)
    ut = []
    # En förälder utanför passet ska inte kunna gömma raden.
    for r in i_ordning:
        if r["parent_id"] is None or r["parent_id"] not in ids:
            ut.append(_rad(r))
            ut += [f"  {_rad(b)}" for b in barn.get(r["id"], [])]
    return ut


def rendera(conn, workout_id, now=None):
    """Passet som text, eller None om det inte finns. conn ska ha row_factory=Row."""
    w = conn.execute(
        """SELECT w.*, s.code sport_code, s.name_sv sport_name, s.detail_table,
                  src.code source_code
           FROM workout w JOIN sport s ON s.id = w.sport_id
           JOIN source src ON src.id = w.source_id WHERE w.id=?""", (workout_id,)).fetchone()
    if w is None:
        return None

    status = fitstatus.fit_status(w["source_code"], w["fit_enriched"], w["started_at"], now)
    L = [f"Pass {w['id']} · {w['local_date']} · {w['sport_name']} ({w['sport_code']})",
         f"Källa: {w['source_code']}",
         f"FIT-berikat: {'ja' if w['fit_enriched'] else 'nej'}",
         f"FIT-status: {status or 'ingen, manuellt loggat'}",
         f"Märkning: {fitstatus.markering(status, w['started_at']) or 'ingen'}",
         f"Simmärkning: {fitstatus.simmarkering(conn, w, now) or 'ingen'}",
         f"Dolt: {'ja, ' + w['deleted_at'] if w['deleted_at'] else 'nej'}",
         f"Dubblett av: {w['duplicate_of_id'] or 'nej'}",
         f"Sporttabell: {w['detail_table'] or 'ingen'}",
         "", "== workout =="]
    rad = conn.execute("SELECT * FROM workout WHERE id=?", (workout_id,)).fetchone()
    L += [f"{k}={_varde(rad[k])}" for k in rad.keys()]

    tabeller = sorted(_tabeller(conn), key=lambda t: (t[0] != w["detail_table"],
                                                      t[0] == "workout_segment", t))
    for tabell, kolumn in tabeller:
        rader = conn.execute(f'SELECT * FROM "{tabell}" WHERE "{kolumn}"=?',
                             (workout_id,)).fetchall()
        namn = f"{tabell}.{kolumn}" if tabell == "workout" else tabell
        L.append("")
        if not rader:
            L.append(f"== {namn}: inga rader ==")
            continue
        L.append(f"== {namn}: {len(rader)} rader ==" if len(rader) != 1 else f"== {namn}: 1 rad ==")
        if tabell == "workout_segment":
            L += _segment(rader)
        elif len(rader) == 1:
            L += [f"{k}={_varde(rader[0][k])}" for k in rader[0].keys()]
        else:
            L += [_rad(r) for r in rader]
    return "\n".join(L)
