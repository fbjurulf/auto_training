import json, sqlite3

conn = sqlite3.connect("traning.db")
rows = conn.execute("SELECT payload FROM raw_import WHERE status='received'").fetchall()

def collapse(o):
    out = {}
    for k, v in o.items():
        if isinstance(v, list):
            first = json.dumps(v[0], ensure_ascii=False)[:300] if v else "tom"
            out[k] = f"<<lista, {len(v)} element, forsta: {first}>>"
        else:
            out[k] = v
    return out

seen = set()
for (p,) in rows:
    for w in json.loads(p)["data"]["workouts"]:
        n = w.get("name")
        if n in seen:
            continue
        seen.add(n)
        print("=" * 20, n)
        print(json.dumps(collapse(w), indent=2, ensure_ascii=False)[:4000])
        print()
