# Testdata: riktiga pass med facit

**HAE-filerna innehåller GPS-spår från riktiga pass:** `route` med
koordinater för löp- och vandringspassen. Repot är privat, och de är
committade orörda med avsikt (beslut 2026-11-03). Ska repot delas måste
de hanteras först, också i historiken. FIT-filernas koordinater är
nollade. Simpassen är inomhus och saknar GPS.

## Innehåll

- `fit/`: FIT-filerna ur `fit/` på Pi:n med koordinaterna nollade av
  `tests/anonymisera_fit.py`, CRC omräknad. De är alltså inte identiska
  med produktionens filer, och ska inte vara det: en fixtur ska vara
  stabil och committad. Inget test läser koordinater. Avkodat fält för
  fält skiljer sig inget annat fält från originalet, och tolkningen ger
  samma rader i alla tabeller (2026-11-05).
- `hae/`: ett workout-objekt per fil, oförändrat ur
  `raw_import.payload` i produktionen, gzippat. Filnamnet anger dag,
  sport och vilket `raw_import.id` det kommer från. Hela payloads
  sparas inte: HAE skickar ett rullande fönster, så en payload på upp
  till 16 MB innehåller mest andra pass.
- `replay.json`: händelserna i produktionens ordning. För varje pass
  finns den första och den sista HAE-payloaden som innehöll det, och
  FIT-filen vid den tidpunkt då den först sågs. Ordningen avgör vilken
  källa som skapar passet och vilken som skriver sist.
- `facit.json`: förväntade värden, avlästa i Träningsappen.

`tests/replay.py` bygger databasen med `build_db()`. Sedan spelar den
upp händelserna genom de riktiga `parse_hae.main()` och
`parse_fit.main()`. Resultatet verifierades 2026-11-03 mot Pi:ns
databas och var identiskt för alla elva pass: källa, tider, distans,
puls, segment, simstatistik och zontid.

## Passen

| Dag | Sport | Varför |
|---|---|---|
| 2026-09-30 | simning | set med 10 s stopp, längsta utan stopp |
| 2026-10-04 | löpning | 32 km, dubblettrisk HAE/FIT |
| 2026-10-07 | simning | korta stopp, A6 |
| 2026-10-14 – 10-17 | löpning, vandring ×3 | v42 |
| 2026-10-18 | löpning | paus, v42 |
| 2026-10-19 | simning | A6, uppvärmning |
| 2026-10-21 | simning | HAE skriver över FIT-berikningen |
| 2026-10-22 | löpning | varv och timerblock |

## Regler

- Facit ändras bara när Träningsappen visar något annat. Ett rött test
  är en avvikelse som ska rapporteras, inte ett test som ska justeras.
- Mått som inte finns än (A6, integritetsvarningen) hoppas över med
  skäl. De får inte se gröna ut.
- `exercise` och `strength_set` är tomma i produktionen. Styrkefixturer
  måste därför ta med egna övningar.

## Köra

Beroendena är pinnade i `requirements.txt`, ur Pi:ns `.venv`. FIT-tolkning
är versionskänslig: en annan `fitdecode` kan visa sig som ändrade facit-tal
utan att någon kod ändrats.

    python -m venv venv && venv/bin/pip install -r requirements.txt
    venv/bin/python -m unittest discover -s tests -t . -v
