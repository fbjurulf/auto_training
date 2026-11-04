# Träningsdatabasens rapporter — kravspec v2

Ersätter den tidigare specen. Tre avsnitt var fel i den: A3 beskrev fel
orsak, del B utgick från fält som inte finns, och arbetsordningen var inte
beroendeordnad. Allt det är rättat här, och det som redan är i drift står
under "Gjort" så att det inte planeras om.

**Arbetssätt:** läs koden, föreslå en plan, vänta på godkännande innan du
ändrar något. En del i taget, med testerna körda efter varje. Visa
kommandon innan du kör dem mot Pi:n och faktisk utskrift efteråt — aldrig
en beskrivning av vad du tror hände.

**Grundregel:** en siffra som ser rimlig ut men är fel är värre än en
siffra som saknas.

---

## Gjort och i drift

- **F1 vakthund.** Larmar vid utebliven import, utebliven hälsodata,
  rclone-fel, nere tjänster och hoppade FIT-filer. Bevisad i produktion.
- **E-fixturer.** `tests/data/` med FIT-filer, HAE-payloads och
  `facit.json`. `tests/replay.py` spelar upp passen genom de riktiga
  parsrarna. Sviten har 40 tester.
- **Schema i repot.** `sql/schema_base.sql` och `sql/seed_reference.sql`
  genererade ur produktionen. Varje schemaändring går nu som numrerad
  migration i `sql/migrations/`.
- **A3 + A3a.** FIT är sanningskälla. `workout.fit_enriched` styr att HAE
  bara fyller tomma fält och aldrig ersätter segment, simstatistik eller
  löpdetaljer. Pass 83, 88 och 91 reparerade.
- **A1.** Blockdetekteringen. Orsaken var inte en paus utan resume, utan
  att `session/stop_all` ignorerades så att sista blocket aldrig stängdes.
  `blockdata.py` delas av weekly och notify. Block utan innehåll slås ihop
  med fotnot. 40 pass omtolkade.
- **Drift.** `ingest_app.py` kör pipelinen under `flock`. 218 dubblettrader
  i `raw_import` markerade `ignored`. Alias för längdskidor, badminton och
  vandring inlagda.

---

## A2a. Skilj autopaus från manuell paus — utredning

Utredning, inte implementation. Rapportera fynden och vänta.

Vid manuell paus fortsätter sträckan räknas mot samma kilometer, så varven
spänner över pausen. Vid autopaus stannar både tid och sträcka, och varven
gör det inte. Generaliseras A2 till alla pass med paus jämförs autopausade
pass mot fel referens.

- Läs ut alla `event` / `event_type`-par ur samtliga FIT-filer. Förekommer
  `timer/stop` utan `_all`, eller någon annan typ, som kan skilja autopaus
  från manuell paus?
- Går de inte att skilja: välj referens utifrån om varvens distanssumma
  matchar totaldistansen inom 2 %. Gör den det har varven inte spänt över
  pausen.
- Skriv ut i rapporten vilken referens som använts.

Notera att A1 visade att hypotesen i förra specen var fel. Utgå från
filerna, inte från vad som verkar rimligt.

## A2. Pass med paus hanteras olika

Löpning 2026-10-18 (10:28 paus) mot 2026-10-14: 14/10 fick blocktabell och
korrekt förklaring. 18/10 fick ingen blocktabell, och integritetskontrollen
skrev att varven summerade till 1:16:15 mot aktiv tid 1:06:05 och att
skillnaden "inte är förklarad av paus". Skillnaden _är_ pausen.

Samma regel för alla pass med registrerad paus, enligt vad A2a visar.
Integritetsvarningen slår bara till om avvikelsen inte förklaras av paus
inom ±30 s.

## A8. Dubbletten 2026-10-04

Facit säger att passet ska finnas en gång. Matchningen sker på
HealthKit-UUID, annars starttid inom fem minuter och samma sport. Åtta
FIT-filer saknar UUID och matchas på starttid.

- Ta reda på vilken rad som kommer varifrån och om båda har UUID.
- Åtgärda orsaken, inte raden.
- Lägg en kontroll som flaggar pass av samma sport som överlappar mer än
  50 % i tid.

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
- Ta bort läsningen av `lap.total_ascent` och `elevationDown`. Båda är död
  kod som ser ut att fungera.

Jämför passets härledda stigning mot `session.total_ascent`, som finns och
är Apples eget värde. Avviker de mycket är tröskeln fel satt.

Nedförsmeter är det viktiga för benen: tre dagars vandring med cirka
2 500 m nedför gav tydliga spår i återhämtningsdatan.

---

## D. En kodväg, inte två

Sammanfattningen och veckorapporten ska använda samma funktioner för varje
värde. Formateringen får skilja sig, beräkningarna inte. `blockdata.py` är
mönstret.

Kända dubbelberäkningar att åtgärda:

1. **Settempo för simning** räknas på varvets hela tid, medan rubriktempot
   räknas på aktiv simtid. Settempot ska räknas på de aktiva längdernas tid
   inom varvet. För 10-21 är skillnaden 2:15 mot 2:03 per 100 m.
2. **Tempo per kilometer** räknas i `notify.py` med en egen funktion och i
   `weekly.py` på sitt eget sätt. Ingen gemensam funktion finns.
3. **Höjdmeter** (del B) ska byggas som en delad funktion från början.
4. **Pulszoner** läses från databasen av båda, vilket är rätt — men
   täckningsgraden räknas på två ställen.

**Dolda pass:** alla nya frågor går mot vyerna. Går något direkt mot
`workout` ska `deleted_at IS NULL` och `duplicate_of_id IS NULL` stå med
explicit.

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
ligger all zontid i Z1–Z2, så andelen blir 100 % oavsett.

## A5. Simningens pulsdata är för gles

Handledspuls i vatten täcker ofta 19–58 % av passet. Är täckningen under
70 % ingår passet inte i 80/20-andelen. Täckningen visas per pass som nu.

## A6. Längsta sammanhängande simsträcka: två mått

`VILA_MIN_S = 12` ligger kvar. Visa båda:

- **Längsta utan stopp** (stopp ≥ 12 s bryter)
- **Längsta med korta stopp** (stopp under 30 s bryter inte)

Det andra måttet blir stort på intervallpass med 20 sekunders vila. Det är
okej — gissa inte vilka stopp som är vila.

Kräver `longest_with_short_stops_m REAL` i `workout_swim` plus migrering
som räknar om befintliga pass. Trösklarna som namngivna konstanter på ett
ställe.

Två testfall väntar på kolumnen: 10-07 ska ge 1000 m och 10-19 ska ge
1200 m med korta stopp.

## A7. Mindre saker

- Varv kortare än 100 m mitt i ett pass (19 m den 2026-10-11, troligen regn
  på skärmen) slås ihop med nästa varv och markeras med fotnot.
  `blockdata.py` har redan funktionen — återanvänd den.
- Exkludera de första 90 sekunderna när maxpuls räknas. Visa råvärdet inom
  parentes.
- "Veckan är pågående" visas på söndagskvällar trots att alla sju dagar
  finns. Skilj på _veckan är komplett_ och _hälsodata saknas ännu för …_.

## A7a. Maxpulsregeln måste slå igenom på zontiden

Annars står "maxpuls 152" bredvid "Z5 0:40" — två tal ur samma rådata som
motsäger varandra. Samma 90-sekundersfönster exkluderas för maxpuls,
zontid, pulstäckning och varvens maxpuls. Pulstäckningen räknas mot
passets tid minus de 90 sekunderna. Visa råvärdet i parentes också vid
zontiden: "Z5 0:00 (0:40 inkl. första 90 s)".

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

## Ordning

**A2a → A2 → A8 → B → D → C → A4 → A5 → A6 → A7 + A7a → F2 → F3 →
omkörning av v40–v43 med skillnaden mot de gamla versionerna.**

A2a är en utredning som avgör hur A2 byggs. B före D eftersom
höjdfunktionen är ett av D:s fall. D före C eftersom C annars skrivs två
gånger. F3 sist av det tekniska, eftersom B måste ha sparat höjdmetrarna
först.

Testerna körs efter varje del. Sviten har i dag ett rött test (A4) och tre
överhoppade (A6 ×2, A2:s integritetsvarning). De blir gröna av att felen
åtgärdas, inte av att testen justeras — om ett förväntat värde inte stämmer
med databasen, säg till i stället för att anpassa testet.
