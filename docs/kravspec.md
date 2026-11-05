# Träningsdatabasens rapporter — kravspec v2

Ersätter den tidigare specen. Tre avsnitt var fel i den: A3 beskrev fel
orsak, del B utgick från fält som inte finns, och arbetsordningen var inte
beroendeordnad. Allt det är rättat här, och det som redan är i drift står
under "Gjort" så att det inte planeras om.

**Arbetssätt:** läs koden, föreslå en plan, vänta på godkännande innan du
ändrar något. En del i taget, med testerna körda efter varje. Visa
kommandon innan du kör dem mot Pi:n och faktisk utskrift efteråt — aldrig
en beskrivning av vad du tror hände.

**Specen följer verkligheten:** när en utredning motsäger specen rättas
specen i samma commit som fynden, så att nästa session läser rätt
premisser.

**Grundregel:** en siffra som ser rimlig ut men är fel är värre än en
siffra som saknas.

---

## Gjort och i drift

- **F1 vakthund.** Larmar vid utebliven import, utebliven hälsodata,
  rclone-fel, nere tjänster och hoppade FIT-filer. Bevisad i produktion.
  Sedan 2026-11-04 också pass som saknar FIT-fil efter 24 h (bara
  HAE-data), larmat dagligen i 14 dagar. A3 krävde det, men det saknades:
  pass 94 (10-27) gick tyst i åtta dagar.
- **E-fixturer.** `tests/data/` med FIT-filer, HAE-payloads och
  `facit.json`. `tests/replay.py` spelar upp passen genom de riktiga
  parsrarna. Sviten har 140 tester, körbara ur en färsk klon med en ny venv
  ur `requirements.txt`. Koordinaterna är nollade i fixturerna, se
  `tests/data/README.md`.
- **Schema i repot.** `sql/schema_base.sql` och `sql/seed_reference.sql`
  genererade ur produktionen. Varje schemaändring går nu som numrerad
  migration i `sql/migrations/`.
- **A3 + A3a.** FIT är sanningskälla. `workout.fit_enriched` styr att HAE
  bara fyller tomma fält och aldrig ersätter segment, simstatistik eller
  löpdetaljer. Pass 83, 88 och 91 reparerade, och 2026-11-04 även 90 (10-20)
  och 93 (10-26), som hittades under A2. Kontrollen i A3 jämförde bara antal
  segment, och för 90 och 93 råkade de stämma. Rätt kontroll är segmentens
  ursprung: HAE:s löpsplittar har
  `pace_s_per_km`, HAE:s simset `pace_s_per_100m` i `extra`, FIT-varv
  `lap_trigger` i `extra`. Stående test: `OriginTests` i `test_facit.py`.
  **FIT-status** (`fitstatus.py`, byggd 2026-11-04): väntar på FIT (under
  24 h), saknar FIT (vakthunden larmar, 24 h–14 dygn), förlorad för FIT
  (äldre). Vakthundens larm och rapportens märkning läser samma funktion, så
  passet syns som HAE-pass även när larmet tystnat. **Kvar att göra:** A3
  krävde också att HAE-pass bara visar det som går att lita på och
  utelämnar härledda värden. Märkningen finns nu, men splittar och simset
  visas fortfarande.
  **Simpass utan längder** (`fitstatus.simmarkering`, 2026-11-05): samma rad
  i sammanfattning och rapport, för HAE-pass och för FIT-filer utan längder
  (pass 76, 10-05: filen har bara record och session). "Längder saknas i
  källfilen" i stället för att måtten saknas tyst. Längdantal, armtag och
  SWOLF visas inte för dessa pass — hos HAE är de distans delat med bana.
  Öppet vatten (pass 74) är ingen varning men säger varför måtten inte finns.
- **A1.** Blockdetekteringen. Orsaken var inte en paus utan resume, utan
  att `session/stop_all` ignorerades så att sista blocket aldrig stängdes.
  `blockdata.py` delas av weekly och notify. Block utan innehåll slås ihop
  med fotnot. 40 pass omtolkade.
- **A8.** Dubbletter. `matchning.py` delas av parse_fit och parse_hae: UUID,
  annars samma sport och starttid inom fem minuter. Varje matchning mellan
  källor sparas i `workout_match` (migration 0003); vakthunden larmar på
  tidsmatchningar för pass som startat det senaste dygnet — passets
  starttid, inte matchningsradens, så att en omtolkning av gamla filer inte
  larmar för redan godtagna pass — och på pass av samma sport som
  överlappar mer än 50 %. Testat: en FIT-fil utan UUID följd av HAE gav två
  pass med den gamla regeln och ger ett nu.
- **Drift.** `ingest_app.py` kör pipelinen under `flock`. 218 dubblettrader
  i `raw_import` markerade `ignored`. Alias för längdskidor, badminton och
  vandring inlagda.

---

## A2a. Autopaus mot manuell paus — utredd 2026-11-04

Specens premiss var att manuell paus låter sträckan räknas vidare så att
varven spänner över pausen, medan autopaus stoppar både tid och sträcka.
Filerna (alla 102) visar något annat:

- **HealthFit märker varje paus som manual.** Autopaus går inte att skilja i
  filerna: 228 `timer/start/manual`, 126 `timer/stop_all/manual`, 102
  `session/stop_all`, inget annat. Inget fält i någon meddelandetyp har
  "auto" eller "pause" i namnet.
- **Sträckan fortsätter inte under pauser:** högst 4,9 m per paus över 110
  pauser, 19 m totalt.
- **Det som skiljer är varvets tid, styrd av `lap_trigger`:** distansvarv
  summerar till aktiv tid (36 pass), egna varvtryck till förfluten tid
  (4 pass: 07-21, 09-20, 10-11, 10-22). Ingen blandning.
- **Kriteriet "distanssumman inom 2 %" diskriminerar inte** — det är alltid
  sant, i de flesta pass på metern.

Rapporttexten om att kilometertempot är otillförlitligt efter paus ("sträcka
som tillgodoräknas under en paus hamnar i nästa kilometer") beskrev en
antagen mekanism, inte en mätning. Borttagen i A2.

## A2. Pass med paus hanteras olika

Ursprungligt symptom: 10-18 fick "varven summerar till 1:16:15 mot aktiv tid
1:06:05 … inte förklarad av paus". Det kom från HAE:s splittar på pass 88 och
försvann när A3a gav passet FIT:s varv. Logiken var ändå fel: kontrollen
jämförde alltid mot aktiv tid, så egna varvtryck med lång paus gav falsk
varning.

Regel (`blockdata.varv_referens`):

- Referensen är den tid — aktiv eller förfluten — som varvens tidssumma
  ligger närmast. Den väljs ur datan, inte ur typen av paus.
- Varning om summan ligger mer än 30 s från den närmaste referensen, eller
  mer än 2 % från både aktiv och förfluten tid. Kontrollen får inte tystna
  bara för att en referens alltid väljs.
- Rapporten skriver passets paustid och vilken referens varven räknas på.
- **Svans är en fotnot, inte en varning** (`blockdata.varv_kontroll`). Går
  hela differensen att lägga efter sista varvet, utan luckor inne i passet,
  står "Varven täcker inte passets sista 44 s." under tabellen. Varvens
  siffror är då rätt, de täcker bara inte hela passet. Luckor inne i passet,
  eller en differens som inte går att placera, ger varning som förut — så
  behåller varningen betydelsen "siffrorna kan vara fel".

I produktionen varnade regeln först för tre pass som den gamla släppte
igenom. Två var svansar: 48 (07-30, FIT, sista 44 s) och 94 (10-27, bara
HAE, sista 36 s) — de får fotnot. Det tredje, 93, var ett A3-offer.

## A8. Dubbletter — utredd och byggd 2026-11-04

**10-04 är inte ett UUID-löst par.** FIT-filen har UUID (`E4F1DA28-…`) och
hör inte till de åtta utan. Förloppet, ur loggarna:

- 11:15Z: FIT skapar pass 75 med UUID:t som `external_id`.
- 15:12Z: HAE-payload 210, som bara innehöll samma pass, ger "1 nya" i
  parse_hae — en HAE-rad med samma UUID. Den dåvarande parse_hae saknade
  spärren mot FIT-pass med samma `external_id`; dagens kod har den.
- Raden finns inte i dag, inte heller i backupen från 11-03 före
  ombyggnaden. Paret delade `external_id`, så det är precis den sort
  `fix_dubbletter.sql` raderade.

Facit (ett pass 10-04) är uppfyllt i fixturerna och i produktionen. Ingen
överlappning av samma sport finns bland de 102 passen i vyn.

**Hålet som finns kvar:** en FIT-fil utan UUID får `digest[:32]` som
`external_id`. Kommer HAE efter FIT skapas en dubblett som varken parse_hae:s
spärr (jämför `external_id`) eller `fix_dubbletter.sql` (raderar bara par med
samma `external_id`; steg 3 listade starttidspar men raderade inget) fångar.
Kommer FIT efter HAE matchar parse_fit på starttid, och då uppstår ingen.
Senaste UUID-lösa filen är från 09-20.

- **Orsaken:** parse_hae ska matcha mot befintliga pass på samma sätt som
  parse_fit gör — UUID, annars samma sport och starttid inom fem minuter.
- **Kontrollen:** pass av samma sport som överlappar mer än 50 % i tid är
  den enda mekanism som hittar ett par utan gemensamt `external_id`. Den
  ska vara stående (vakthunden), inte en engångsstädning.

---

## B. Höjdmeter — omskriven efter B0

**B0 är besvarad, och svaret ändrar hela delen.** Nedförsmeter finns inte
som färdigt tal i någon källa:

- `elevationDown` förekommer inte i någon HAE-payload (22 versioner
  granskade).
- `total_descent` är tom i alla FIT-sessioner.
- `total_ascent` och `total_descent` förekommer aldrig i FIT:s
  `lap`-meddelanden, över 102 filer.
- Höjd finns däremot i `record` (FIT) och i `route` (HAE).

Alltså: **höjdmeter upp och ner härleds ur höjdserien.** En funktion, samma
för båda källorna, enligt del D.

- Summera positiva och negativa höjdskillnader med en tröskel på några
  meter, så att barometerbrus inte summeras. Dokumentera tröskeln.
- Per pass för alla sporter med höjddata, inklusive vandring och promenad.
- Per varv och per kilometer för löpning, vandring och cykel, beräknat
  inom varvets tidsfönster.
- Veckorapportens volymtabell: en kolumn upp och en ner per sport, plus
  summa.
- Ta bort läsningen av `lap.total_ascent` (gjort). **Rättelse:** någon
  läsning av `elevationDown` fanns aldrig i koden — påståendet kom från en
  rapport som upprepade det utan att kontrollera. `elevation_loss_m` har
  helt enkelt aldrig skrivits.

~~Jämför passets härledda stigning mot `session.total_ascent`. Avviker de
mycket är tröskeln fel satt.~~ **Struken 2026-11-04: Apple är ingen
kontrollpunkt.** Apples värde varierar kraftigt på samma rutt — samma 7,1
km-runda gav 10, 93 och 26 m; en 9,3 km-runda 56, 58 och 57 m, sedan 78, 85
och 84 m. Ett värde som inte är stabilt mot sig självt kan inte avgöra om
tröskeln är rätt. Apples tal sparas (`ascent_apple_m`) och visas när det
skiljer mer än 25 %, men formulerat så att det inte läses som facit: "upp
102 m (Apple anger 31 m — Apples värde varierar kraftigt på samma rutt och
är ingen referens)".

**Också struken: rundkontrollen** (upp − ner nära noll på pass som börjar
och slutar på samma ställe). Med hysteres som räknar sista segmentet är upp
− ner per konstruktion höjdskillnaden mellan start och mål, så kontrollen
kan inte skilja metoder åt. Den mäter drift i höjdserien — och den är
4,3–4,5 m i median.

Det som återstår som kontroll är upprepningar av samma runda: en metod som
räknar brus ger spridning mellan gångerna. Underlaget är i dag tunt (två
rundor, nio pass) och växer med varje upprepning.

**Kalibrering 2026-11-04** (62 FIT-pass med höjd och minst 20 m stigning):

- **Samma höjdserie i båda källorna.** FIT `record.enhanced_altitude` och
  HAE `route[].altitude` är båda 1 Hz och ger samma härledda stigning inom
  ett par meter för samma pass. HAE:s `elevationUp` är Apples värde, samma
  som FIT-sessionens `total_ascent`.
- **Serien är redan utjämnad — "några meter" är för mycket.** Vid 5 m blir
  medianen 45 % av Apples värde; redan vid 2 m räknas vandringarna i v42
  för lågt (536 mot 651 m).
- **Ingen inställning träffar alla pass.** Bäst: glidande medel 11 s, ingen
  tröskel — median 14 % fel, 34 av 62 inom ±15 %, vandringarna 0,94–1,15.
  Kvar finns flacka löppass där härlett blir 1,4–3,5 gånger Apples värde
  (2026-03-21: 102 mot 31 m). Där dominerar bruset, och ingen tröskel som
  inte samtidigt kapar riktiga backar tar bort det.
- v42:s tre vandringar ger 2 223 m nedför med den valda inställningen —
  specens "cirka 2 500 m" var en uppskattning.

**Hysteresförsök och val av inställning:** riktningsföljande hysteres (räkna
först när vändningen överstiger tröskeln) provades med och utan 11 s
utjämning, 1–5 m. Tre kontroller: fel mot Apple, |upp − ner| på rundor (42
pass), spridning mellan upprepningar av samma runda (2 grupper, 9 pass —
tunt underlag). Fel mot Apple och rundkontrollen redovisas nedan men är
strukna som kontrollpunkter, se ovan.

- Upp − ner på rundor blev 4,3–4,5 m med utjämning oavsett metod, som
  väntat när upp − ner per konstruktion är höjdskillnaden start–mål.
- Hysteres 3 m räknar systematiskt lågt: löppass 0,80 av Apple i median
  (7,4 mot 9,7 m/km), spridning mellan upprepningar 29 % mot 17 %.
- **Apple är inte stabilt på samma runda:** en 7,1 km-runda gav 10, 93 och
  26 m; en 9,3 km-runda 56–58 m tre gånger, sedan 78–85 m. Apple är därför
  ingen facit på flacka pass, vilket stöder variant (a).
- **Valt: 11 s glidande medel, hysteres 0 m** (`hojd.py`). Minst spridning
  mellan upprepningar (17 % mot 29 % för hysteres 3 m), och närmast Apple
  även om det inte längre är ett krav. Konstanterna `UTJAMNING_S` och `HYSTERES_M` på ett ställe.

**Byggt 2026-11-04:** `hojd.py` delas av parse_fit (record), parse_hae
(route), weekly och notify. Migration 0004 lägger till `workout.ascent_m`,
`descent_m`, `ascent_apple_m` och `workout_segment.elevation_loss_m`, och
bygger om `workout_segment` så att `kind` tillåter `km` — CHECK går inte att
ändra utan ombyggnad. Km-segment räknas vid tolkning för löpning, vandring,
cykel och promenad, med rörelsetid (glapp över 10 s är paus). Simning får
ingen höjd. **Utgående:** `workout_run.elevation_gain_m` och
`elevation_loss_m` skrivs inte längre; droppas efter en vecka med rena
körningar. HAE-pass får höjd per pass och per split men inga egna
km-segment — HAE:s splittar är redan per kilometer.

Nedförsmeter är det viktiga för benen: tre dagars vandring med cirka
2 500 m nedför gav tydliga spår i återhämtningsdatan.

---

## D. En kodväg, inte två

Sammanfattningen och veckorapporten ska använda samma funktioner för varje
värde. Formateringen får skilja sig, beräkningarna inte. `blockdata.py` är
mönstret.

Kända dubbelberäkningar att åtgärda:

1. **Settempo för simning** — gjort. En regel för stopp, `passdata.VILA_MIN_S`
   (12 s) på uppehållet mellan två aktiva längder, styr både längsta
   sammanhängande sträcka (A6) och settabellen. Settid = varvtid minus stopp
   inne i setet; vändningar räknas som simning. Stopp inne i ett set märks
   "2:05*" med fotnot om avbrottet. Rubrikens simtid är summan av setens
   tider, så rubrikens och setens tempo går ihop (10-21: 2:12/100 m, var
   2:03 mot set på 2:15). Stopp avgörs vid tolkningen på FIT-längderna och
   sparas (`avbrott_s` på set, `stopp` på vilovarv) — räknat ur databasens
   sekundavrundade tider hade fotnoten och A6 kunnat säga olika saker om
   samma paus. Två kopior av gränsen fanns (`parse_fit.VILA_MIN_S`,
   `weekly.VANDNING_MAX_S`); båda borta. Test: `test_simstopp` visar att
   båda följer gränsen när den flyttas.
   **Kanttid:** tid före första längden och efter sista räknas mot samma
   gräns och dras från settiden, men får egna ord: "* Set 1: 1:27 före
   start" respektive "efter sista längden". En sen start är inte ett brott i
   simningen och kan inte bryta en sammanhängande sträcka. I fixturerna
   finns den bara i första setet: 15, 87, 29 och 29 s. Stopp på 10–11,9 s
   räknas som vändning; accepterat, gränsen bor på ett ställe.
   **Drar regeln bort simning? Mätt 2026-11-05, regeln oförändrad.** 23 pass
   har före-start-tid, alltid i set 1; 22 går att jämföra (79 har ett set).
   Set 1 mot övriga set är 16 av 22 snabbare, median −10 s/100 m (p = 0,052),
   men set 1 är oftast 25–50 m och korta set är snabba i sig (median 25 m
   2:05, 50 m 2:14, 100 m 2:18). Mot set av samma distans i samma pass: 13 av
   19, −4,9 s; placebon set 2 mot senare set ger 12 av 19, −3,1 s (inte
   särskiljbart). Pulsen under före-start-tiden sjunker eller står still i de
   flesta pass, också de längsta (222 s: 96→85; 233 s: 88→77; 10-21:s 29 s:
   96→80), och stiger först när längderna börjar. Klart kvar: längd 1 i set 1
   är 2,1 s kortare och har ett armtag färre än längd 1 i senare set (16 av
   20, p = 0,012; i set 2 −0,5 s). Fräsch start och 2 s sen detektering går
   inte att skilja åt; även i det senare fallet är det 2 s per pass, inte det
   regeln drar av. 10-21:s set 1 (1:44) är snabbast av set 1 på 50 m, men i
   samma ände finns 1:52, 1:55 och 1:55. Tempotestet ser inte en helt missad
   längd — då försvinner både tid och meter; pulsen är enda tecknet mot det,
   och handledspuls i vatten är ett svagt tecken.
   **Definitionsbyte:** rubrikens simtempo blir långsammare i alla simpass
   (10-21: 2:03 → 2:12/100 m). Veckorapporten för v45 får en rad överst om
   det (`weekly.DEFINITIONSBYTEN`).
2. **Tempo per kilometer** — gjort: `passdata.tempo_per_km`.
3. **Höjdmeter** — gjort i del B: `hojd.py`.
4. **Pulstäckning** — gjort: `passdata.pulstackning` och `visa_tackning`.
   Veckonivåns täckning (gräns 99,5 %) hör till A4/A5.

Stående test: `tests/test_samma_tal.py` jämför de renderade talen i notify
och weekly för varje pass i fixturerna — blocktempo, pulstäckning, höjd,
snitttempo. Det jämför texterna, inte funktionerna, så att det syns om en
modul börjar räkna själv igen. Verifierat att det blir rött när notify räknar
annorlunda.

**Dolda pass:** alla nya frågor går mot vyerna. Går något direkt mot
`workout` ska `deleted_at IS NULL` och `duplicate_of_id IS NULL` stå med
explicit. Granskat 2026-11-04: alla direkta frågor filtrerar eller arbetar
med avsikt på ett bestämt id. **Undantag med avsikt:** UUID-steget i
`matchning.py` får inte filtrera — en dold rad ska blockera återimport, och
filtreras den bort kommer passet tillbaka.

---

## C. Utförlig sammanfattning efter varje pass

Ungefär samma detaljnivå som veckorapportens passavsnitt, plus jämförelser.
Byggs på D:s delade funktioner, inte bredvid dem.

### Löpning

**Rubrik:** datum, distans, aktiv tid, förfluten tid, paus, snittfart,
snitt- och maxpuls, höjdmeter upp/ner, kadens, steglängd, marktid, effekt,
datakälla.

**Tabell:** en rad per kilometer med tid, fart, snitt- och maxpuls,
höjdmeter upp/ner. Egna varvtryck ger varvtabell i stället. Pass med paus
visar blocktabellen först och kilometertabellen under.

**Zoner:** Z1–Z2, Z3, Z4, Z5 och pulstäckning i procent.

**Jämförelse:** puls vid samma fart mot de senaste fyra veckornas lugna
pass — mitt bästa enskilda mått på aerob form. Kilometrar i fartspannet
±10 s kring passets snittfart, bara pass utan paus, och exkludera
kilometrar med mer än 15 höjdmeter stigning.

Visa underlaget: "5:45/km på puls 140, mot 143 på 4-veckorssnittet
(underlag 7 km denna vecka, 34 km i baslinjen)." Under fem kilometer i
endera perioden: visa ingen jämförelse, skriv ut varför.

### Simning

**Rubrik:** datum, total distans, simtid, vila, totaltid, tempo per 100 m
på simtiden, snitt- och bästa SWOLF, armtag per längd, andel frisim,
datakälla.

**Längsta sträcka:** båda måtten från A6 i fetstil överst.

**Settabell:** distans, tid, tempo/100 m, vila efter, SWOLF, armtag per
100 m, stil. Vilorader under `VILA_MIN_S` märks _vändning_.

**Analys:** armtag första mot andra halvan av huvudsetet; spridning i tempo
för set med samma distans ("8×100: 2:05–2:20, spridning 15 s"); snittvila
mellan set av samma distans.

### Vandring och promenad

Distans, aktiv tid, förfluten tid, höjdmeter upp och ner, snitt- och
maxpuls.

### Cykel

Distans, tid, snittfart, snitt- och normaliserad effekt om den finns,
kadens, höjdmeter, zoner, tabell per varv eller per 10 km.

Trainern kommer i december. Verifiera tidigt hur den exporterar: loggar den
i en egen app (Zwift, MyWhoosh, Rouvy) och jag inte bär klockan, hamnar
effekt och kadens aldrig i HealthKit. Skriver den FIT till en mapp räcker
samma `fit/`-mapp, samma parser och en rad i `sport_alias`. Effektzoner är
ett eget begrepp vid sidan av pulszoner och kräver FTP — utanför specen
tills trainern finns.

### Styrka

Tid, snitt- och maxpuls.

### RPE och noteringar

Visa i både sammanfattningen och veckorapportens passavsnitt. Direkt efter
importen är fälten tomma — visa dem inte då, men lägg en rad om hur de
fylls i: `/rpe 7` och `/kommentar <id> …`. Veckorapporten listar pass utan
RPE.

### Format

- Telegram renderar inte markdowntabeller. `parse_mode=HTML`, tabeller i
  `<pre>`, rubriker i `<b>`. Testa på mobil.
- **Escapa all dynamisk text** med `html.escape()`: noteringar,
  kommentarer, sportnamn, filnamn i felmeddelanden, allt i `<pre>`. Mina
  kommentarer kan innehålla `<`, `>` och `&`, och Telegram svarar då HTTP
  400 "can't parse entities" — som `requests.post` inte kastar på, så
  meddelandet försvinner tyst. Kontrollera statuskoden och larma vid annat
  än 200.
- Gränsen är 4096 tecken. Ett långpass med 32 kilometerrader slår i taket:
  dela i två meddelanden (rubrik och zoner först, tabell sedan) i stället
  för att kapa. Dela aldrig mitt i ett `<pre>`-block.
- Mail kan vara HTML med riktiga tabeller.

---

## A4. Vandring gör andelen lugn träning missvisande

v42 med 12 timmar vandring visade "100 % lugnt". 80/20-andelen ska räknas
bara på löpning, cykling och simning.

Skriv inte sportlistan i koden — lägg kolumnen `counts_in_intensity_ratio`
i `sport`-tabellen. Vandring, promenad och styrka visas i zontabellen men
ingår inte i procentsiffran.

Lägg en rad under procentsiffran om vad den bygger på: "Räknat på 4:12:00
löpning och simning. Utanför: 12:04:00 vandring, 0:45:00 styrka."

Testet för v42 testar underlagsraden, inte procentsiffran — den veckan
ligger all zontid i Z1–Z2, så andelen blir 100 % oavsett. Procenten testas i
`tests/test_zonandel.py` på en vecka där vandringen gör skillnad.

**Räkna från passen, inte från detaljtabellen (gäller A4, A5 och A7).** Pass
74 och 76 saknar rad i `workout_swim`, och kontrollen som gick via den
tabellen validerade bara pass som hade en rad. Testerna ska utgå från
`v_workout` och vänstersammanfoga, så att ett saknat pass syns som saknat i
stället för att falla ur urvalet.

**Byggd 2026-11-05.** Migration 0007 lägger till kolumnen; löpning, löpband,
cykel, spinning och simning räknas. Rapporten skriver "Räknat på 2:18:52
löpning av veckans 14:28:53. Utanför: 12:10:01 vandring." Övriga sporter står
kvar i volymtabellen och i zontabellen, märkta _utanför_, och tabellens summarad
heter _I andelen_ och är samma tal som rubriken. Pass utan zonrader (76 i v41, 94
i v44) ligger nu i nämnaren och nämns vid namn; de föll förut ur "Underlaget är
…". En vecka utan zontid i sporterna som räknas får ingen procent i stället för
0 %. Längdskidor ingår inte (facit listar inte sporten). Rapporten för v45 får en
rad överst om definitionsbytet.

## A5. Simningens pulsdata är för gles

Handledspuls i vatten täcker ofta 19–58 % av passet. Är täckningen under
70 % ingår passet inte i 80/20-andelen. Täckningen visas per pass som nu.
Ett pass utan zonrader ska synas bland undantagen, inte falla bort — se
regeln under A4.

## A6. Längsta sammanhängande simsträcka: två mått — byggd 2026-11-04

- **Längsta utan stopp:** uppehåll på minst `passdata.VILA_MIN_S` (12 s)
  bryter.
- **Längsta med korta stopp:** uppehåll under `passdata.KORTA_STOPP_S`
  (30 s) bryter inte.

Samma funktion (`passdata.langsta_svit`) med olika gräns, och samma
uppehållsregel som settabellens fotnoter (D1). Kolumnen
`longest_with_short_stops_m` kom med migration 0005, som också bygger om
`v_swim` — vyn har fast kolumnlista. Testfallen är gröna: 10-07 ger 1 000 m
och 10-19 ger 1 200 m med korta stopp. Det andra måttet blir stort på
intervallpass med kort vila; det är avsett.

## A7. Mindre saker

- Varv kortare än 100 m mitt i ett pass (19 m den 2026-10-11, troligen regn
  på skärmen) slås ihop med nästa varv och markeras med fotnot.
  `blockdata.py` har redan funktionen — återanvänd den.
- Exkludera de första 90 sekunderna när maxpuls räknas. Visa råvärdet inom
  parentes.
- "Veckan är pågående" visas på söndagskvällar trots att alla sju dagar
  finns. Skilj på _veckan är komplett_ och _hälsodata saknas ännu för …_.

Testerna följer regeln under A4: från `v_workout` med vänstersammanfogning.

## A7a. Maxpulsregeln måste slå igenom på zontiden

Annars står "maxpuls 152" bredvid "Z5 0:40" — två tal ur samma rådata som
motsäger varandra. Samma 90-sekundersfönster exkluderas för maxpuls,
zontid, pulstäckning och varvens maxpuls. Pulstäckningen räknas mot
passets tid minus de 90 sekunderna. Visa råvärdet i parentes också vid
zontiden: "Z5 0:00 (0:40 inkl. första 90 s)".

## A9. Rimlighetskontroll vid import — byggd 2026-11-05

Pass 43 (16 h, 800 m) och 79 (63 min, 25 m) kom in utan att något reagerade,
och 43 bidrog med 9 774 s simtid till varje summa och trend. `rimlighet.py`
varnar i Telegram och avvisar aldrig: passet ligger i databasen som vanligt.

- Körs sist i `notify`, i varje pipelinekörning även utan nya pass, eftersom
  FIT-filen ofta kommer efter att HAE-passet aviserats.
- Migration 0006 lägger till `workout.rimlighet_varnad`, texten som senast
  varnades. Varnar bara när texten ändras, så en omtolkning av gamla filer inte
  larmar igen. `rimlighet.py --baslinje` godtar dagens värden och körs en gång
  efter migrationen.
- Gränser per sport: längsta tid, tempo över hela passet (vila inräknad), kortast
  60 s, maxpuls över 220, och banlängd skild från 25, 33,33 och 50 m. Går från
  `v_workout` med vänstersammanfogning, så pass utan rad i `workout_swim` kontrolleras.
- Mot produktionen 2026-11-05 faller 43 utanför (tid och tempo), 79 likaså men
  det är dolt, och 58 (08-12) på banlängden 52 m.
- **Pass 58 utrett, inte rättat.** 57,7 s och 24,5 armtag per längd, mot 32 s och
  12 armtag i 25 m-passen, vid samma takt (25 armtag/min). På 25 m vore det 3:51/100 m
  och 1,0 m/armtag, mot passens 1:58–2:18 och 1,9–2,5 m. Armtagen ger 52 m, tempot
  45 m. Passet är ingen 25 m-bana, och FIT-filen säger 52 m och utomhus. Exakt
  längd går inte att avgöra ur datan.

---

## F2. Backup

`sqlite3 .backup` till tidsstämplad fil, 14 dagars rotation, kopia utanför
Pi:n via rclone. Larma om senaste lyckade backup är äldre än två dygn.

## F3. raw_import växer

Databasen är 735 MB efter knappt två månader. GPS-rutten i payloaden
dominerar. **Får inte köras före del B** — rutten är enda källan till
höjdserien för HAE-pass. När höjdmetrarna räknas fram vid tolkning och
sparas är rutten förbrukad.

Nolla `payload` på rader äldre än 90 dagar med status `parsed`, behåll
hash, storlek och tidsstämpel. VACUUM efteråt, ny backup, och verifiera
att antalet pass är oförändrat. VACUUM behöver ungefär lika mycket ledigt
utrymme som databasen är stor.

---

## /veckorapport — byggd 2026-11-05

`/veckorapport` ger innevarande vecka till nu, `/veckorapport 44` vecka 44 i sin
helhet, som fil precis som söndagsrapporten. Samma beräkning:
`weekly.build_report` tar sluttidpunkten `slut`, och boten har ingen egen.
Test: `tests/test_veckorapport.py` jämför kommandots fil för söndag 23:59 med
vad `weekly.main` skriver söndag 20:00, tecken för tecken.

- **Delvis vecka:** första raden är "Vecka 45, delvis - måndag till torsdag
  13:55.", och under volymtabellen står att jämförelserna ställer delveckan mot
  hela veckor.
- **Söndag räknas som hel vecka.** Söndagsrapporten skrev förut "Veckan är
  pågående", eftersom den körs på veckans sista dag. Den säger nu "Veckan är
  komplett". Det är den delen av A7; resten ligger kvar på is.
- **Läser bara:** skrivskyddad anslutning, ingen rapportfil, inget `notified_at`.
- Ingen förhandsbekräftelse: längsta veckan tar 0,015 s att räkna på Pi:n.

---

## Ordning

**Stängt 2026-11-05.** A2a, A2, A8, B, D, C, A4, A6 och A9 är byggda. **A5, A7,
A7a, F2 och F3 ligger på is** tills data visar att något är fel; avsnitten ovan
står kvar som underlag. Fel letas inte i förväg: ett pass som ser tokigt ut i en
rapport är startpunkten. **Omkörning av v40–v43 görs inte:** trenden räknas ur
databasen vid varje rapport och blir rätt utan omkörning.

Känt och lämnat: täckningsmeningen ("handledsmätningen tappar kontakt") står också
när orsaken är ett pass utan zondata, som 94 i v44. Passet nämns på raden ovanför.

Testerna körs efter varje del, i en färsk klon med en ny venv ur
`requirements.txt`. Inga överhoppade. Röda test blir gröna av att felen
åtgärdas, inte av att testen justeras — om ett förväntat värde inte stämmer
med databasen, säg till i stället för att anpassa testet.
