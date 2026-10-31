import sys, collections, fitdecode

path = sys.argv[1]
typer = collections.Counter()
exempel = {}

with fitdecode.FitReader(path) as fit:
    for frame in fit:
        if frame.frame_type != fitdecode.FIT_FRAME_DATA:
            continue
        typer[frame.name] += 1
        if frame.name not in exempel:
            exempel[frame.name] = {f.name: f.value for f in frame.fields}

print("Meddelandetyper och antal:")
for namn, antal in typer.most_common():
    print(f"  {namn}: {antal}")

for namn in ("session", "lap", "length", "record"):
    if namn in exempel:
        print(f"\n=== {namn} (första) ===")
        for k, v in exempel[namn].items():
            print(f"  {k}: {v}")
