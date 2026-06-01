# GHL Funnel Reporter

EgyszerÅ±, stabil funnel riportolÃ³ projekt GoHighLevel adatokbÃ³l. A script GHL kontaktokat Ã©s a zÃ¡rt kontaktok appointmentjeit hasznÃ¡lja, majd CSV Ã©s HTML riportot generÃ¡l.

## Assumptions

- HasznÃ¡lt API endpointok:
  - `POST /contacts/search` a kontaktok lapozott lekÃ©rÃ©sÃ©re location szinten
  - `GET /locations/:locationId/customFields` a szÃ¼ksÃ©ges custom field azonosÃ­tÃ³k feloldÃ¡sÃ¡ra
  - `GET /contacts/:contactId/appointments` a zÃ¡rt kontaktokhoz tartozÃ³ appointmentek lekÃ©rÃ©sÃ©re
- Custom field azonosÃ­tÃ¡s:
  - elsÅdlegesen a `lead_date`, `first_booking_date`, `show_date`, `close_date`, `lead_status` nevekre keresÃ¼nk
  - a kÃ³d megprÃ³bÃ¡lja a field `id`, `fieldKey`, `key`, `slug` Ã©s `name` Ã©rtÃ©keit is eltÃ¡rolni, majd ezek bÃ¡rmelyikÃ©vel feloldani a kontakt custom field Ã©rtÃ©kÃ©t
  - a kontakt oldalon tÃ¶bb lehetsÃ©ges custom field formÃ¡tumot is kezelÃ¼nk: `customFields` mint lista, `custom_fields` mint lista, illetve dictionary alak
- Appointment szÃ¡mlÃ¡lÃ¡s:
  - meetnek elsÅdlegesen a kontakt appointmentjei kÃ¶zÃ¼l azokat tekintjÃ¼k, ahol a stÃ¡tusz `showed`, `show`, `completed`, `confirmed-show`, `attended` vagy `attended_meeting`
  - ha ezek egyike sem Ã©rhetÅ el stabilan, fallbackkÃ©nt az Ã¶sszes appointment szÃ¡mÃ¡t hasznÃ¡ljuk
- HiÃ¡nyzÃ³ vagy inkonzisztens adatok fallback logikÃ¡ja:
  - hiÃ¡nyzÃ³ custom field esetÃ©n az adott kontakt az adott funnel lÃ©pcsÅben nem szÃ¡mÃ­t bele
  - dÃ¡tum parsingnÃ¡l ISO dÃ¡tum, ISO datetime, epoch timestamp Ã©s nÃ©hÃ¡ny gyakori string formÃ¡tum is tÃ¡mogatott
  - ha a GHL API ideiglenesen hibÃ¡zik vagy rate limitet ad, a kliens retry/backoff logikÃ¡val ÃºjraprÃ³bÃ¡lkozik
  - a riport csak azokat a kontaktokat veszi figyelembe, amelyeknÃ©l legalÃ¡bb egy relevÃ¡ns funnel dÃ¡tum beleesik a vizsgÃ¡lt idÅszakba

## ProjektstruktÃºra

- `main.py`
- `ghl_client.py`
- `report_builder.py`
- `templates/report.html.j2`
- `requirements.txt`
- `.env.example`

## Setup

1. Hozz lÃ©tre virtuÃ¡lis kÃ¶rnyezetet Ã©s telepÃ­tsd a csomagokat:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. MÃ¡sold a `.env.example` fÃ¡jlt `.env` nÃ©vre, Ã©s add meg a GHL API kulcsot Ã©s location ID-t.

3. Opcionális GA4 bekötés napi riporthoz:

- `GA4_PROPERTY_ID=533087750`
- `GA4_CREDENTIALS_PATH=/abszolut/elérési/út/a/service-account.json`

Ha ezek be vannak állítva, a napi HTML riport egy külön GA4 Summary és GA4 Daily Breakdown blokkot is tartalmaz.

4. Opcionális Meta Ads bekötés napi és heti riporthoz:

- `META_AD_ACCOUNT_ID=act_123456789012345`
- `META_ACCESS_TOKEN=...`
- `META_CAMPAIGN_ID=123456789012345`

Ha ezek be vannak állítva, a napi és heti HTML riport Meta Ads összesítő blokkot is tartalmaz.
Ez jelenleg a spend, impressions, clicks, landing page views és leads számokat húzza be, valamint heti nézetben ad set bontást is mutat.

5. FuttatÃ¡s alapÃ©rtelmezett, elmÃºlt 30 napos idÅszakkal:

```bash
python main.py
```

6. FuttatÃ¡s egyedi dÃ¡tumtartomÃ¡nnyal:

```bash
python main.py --start-date 2026-03-01 --end-date 2026-03-31
```

7. HÃ©tfÅi heti Ã¶sszehasonlÃ­tÃ³ riport az aktuális riporthetet a megelőző 7 nappal összehasonlítva:

```bash
python main.py --report-type weekly_compare
```

8. Havi Ã¶sszehasonlÃ­tÃ³ riport az utolsÃ³ lezÃ¡rt Ã¼zleti hÃ³napra, Ã¶sszevetve az azt megelÅzÅ Ã¼zleti hÃ³nappal.
Az Ã¼zleti hÃ³nap itt 15-tÅl a kÃ¶vetkezÅ hÃ³nap 14-ig tart.

```bash
python main.py --report-type monthly_compare
```

## Kimenetek

- `daily_funnel_report.csv`
- `daily_funnel_report.html`
- `archive/daily_funnel_report_YYYY-MM-DD.csv`
- `archive/daily_funnel_report_YYYY-MM-DD.html`
- `weekly_funnel_report.csv`
- `weekly_funnel_report.html`
- `weekly_funnel_report.pdf`
- `monthly_funnel_report.csv`
- `monthly_funnel_report.html`
- `monthly_funnel_report.pdf`
- `period_funnel_report.csv`
- `period_funnel_report.html`
- `period_funnel_report.pdf`
- `report_run.log`

## Historikus Google Drive és Google Sheets mentés

A napi riport sikeres lokális elkészülése után a rendszer opcionálisan historikus mentést végez. Ez külön modulban fut, ezért ha a Drive vagy Sheets mentés hibázik, a lokális HTML/CSV riport akkor is elkészül.

Szükséges környezeti változók:

- `REPORT_HISTORY_ENABLED=true`
- `REPORT_DRIVE_UPLOAD_ENABLED=true`
- `GOOGLE_APPLICATION_CREDENTIALS=/abszolut/elérési/út/google-service-account.json`
- `GOOGLE_SHEET_ID=...`
- `GOOGLE_DRIVE_ROOT_FOLDER_NAME=LionCare`
- `DRIVE_UPLOAD_AUTH_MODE=oauth`
- `GOOGLE_DRIVE_OAUTH_CLIENT_SECRET_PATH=/abszolut/elérési/út/oauth-client-secret.json`
- `GOOGLE_DRIVE_OAUTH_TOKEN_PATH=/abszolut/elérési/út/google-drive-oauth-token.json`

A service accountnak továbbra is hozzáférést kell adni:

- a `LionCare Funnel Historical Data` Google Sheethez szerkesztőként

Drive feltöltéshez nem service accountot használunk, hanem user OAuth 2.0 tokent. Ez azért kell, mert service account normál My Drive mappába nem tud stabilan fájlt feltölteni tárhelykvóta hiánya miatt. A Drive OAuth teljes Drive scope-ot használ (`https://www.googleapis.com/auth/drive`), mert a rendszernek név alapján meg kell találnia a meglévő `LionCare` My Drive mappát és írnia kell az almappáiba. A Sheets historikus mentés ettől függetlenül továbbra is service accounttal fut.

Az OAuth init egyszeri kézi lépés:

```bash
python scripts/google_drive_oauth_init.py
```

Ez böngészőben megnyitja a Google consent flow-t, majd a refresh/access tokent a `GOOGLE_DRIVE_OAUTH_TOKEN_PATH` útvonalra menti. A token fájlt ne commitold és ne küldd tovább.

Fontos: a Drive upload nem használ Shared Drive logikát. A `GOOGLE_DRIVE_ROOT_FOLDER_NAME` egy normál My Drive mappa neve. OAuth módban a rendszer annak a Google felhasználónak a Drive-jában dolgozik, aki az OAuth init során engedélyezte a hozzáférést. Ha nincs OAuth token, a Drive upload egyértelmű hibát ad, de a lokális riport és a Google Sheets mentés továbbra is működik.

Ellenőrző parancsok:

```bash
# 1. OAuth token létrehozása
python scripts/google_drive_oauth_init.py

# 2. Drive upload smoke test
python scripts/google_drive_upload_smoke_test.py

# 3. Napi riport futtatása, Sheets mentéssel és Drive upload kísérlettel
python run_daily_funnel_report.py
```

Sikeres smoke test után a Drive-ban ezeknek kell megjelenniük:

- `LionCare/riport/daily_html/lioncare_drive_upload_smoke_test_*.html`
- `LionCare/riport/daily_csv/lioncare_drive_upload_smoke_test_*.csv`

A rendszer nem használ hardcoded Drive folder ID-t. Futáskor ellenőrzi és szükség esetén létrehozza ezt a struktúrát:

```text
LionCare/
└── riport/
    ├── daily_html/
    ├── daily_csv/
    └── archive/
```

A Drive-ba dátumozott fájlok kerülnek:

- `LionCare/riport/daily_html/daily_funnel_report_YYYY-MM-DD.html`
- `LionCare/riport/daily_csv/daily_funnel_report_YYYY-MM-DD.csv`

A Google Sheet neve javasoltan `LionCare Funnel Historical Data`, a tabokat a kód automatikusan létrehozza és fejlécezi:

- `daily_summary`
- `adset_daily`
- `ghl_status_daily`

Kézi napi futtatás historikus mentéssel:

```bash
python run_daily_funnel_report.py
```

Ha csak lokális riport kell Drive/Sheets nélkül:

```bash
REPORT_HISTORY_ENABLED=false python main.py --report-type daily
```

Cron példa macOS/Linux környezetre, Budapest idő szerint reggel 06:00-kor:

```cron
0 6 * * * cd /path/to/LionCare\ report && /path/to/python /path/to/LionCare\ report/run_daily_funnel_report.py >> /path/to/LionCare\ report/cron.log 2>&1
```

macOS `launchd` példa ugyanarra a 06:00-s napi futásra:

```bash
cp com.lioncare.daily-funnel-report.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.lioncare.daily-funnel-report.plist
launchctl enable "gui/$(id -u)/com.lioncare.daily-funnel-report"
```

Ellenőrzés:

```bash
launchctl print "gui/$(id -u)/com.lioncare.daily-funnel-report"
```

Logok:

- `launchd_daily_report.log`
- `launchd_daily_report.err.log`
- `report_run.log`

GitHub Actions példa is van itt:

```text
.github/workflows/daily_funnel_report.yml
```

Fontos: a GitHub Actions cron UTC-ben fut. A mellékelt workflow `04:00` és `05:00` UTC-kor is elindul, de egy Budapest-idő szerinti guard step csak akkor futtatja a riportot, ha helyileg ténylegesen `06:00` van. Így a téli/nyári időszámítás nem okoz dupla vagy rossz időpontú futást.

## Logging

- Minden futás `report_run.log` fájlba ír időbélyeges státusz sorokat.
- A log tartalmazza a fő lépések futási idejét, a talált kontaktok számát, a weekly user meeting bontáshoz felhasznált appointmentek számát, valamint a sikeres fájlírást vagy esetleges hibát.
- Automatizmus hibaelhárításnál ezt a fájlt érdemes elsőként megnézni.

## ManuÃ¡lis futtatÃ¡s

- Általános kézi futtatás teljes backup szinkronnal:

```bash
./run_manual_report_and_sync.sh --report-type daily --start-date 2026-04-01 --end-date 2026-04-11
```

Ez mindig a OneDrive-os `LionCare report` projektből fut, ott írja a friss outputokat, majd a teljes projektet leszinkronizálja a `/Users/hidvegi/Documents/New project` mappába biztonsági másolatként.

- Ha ugyanolyasmi összehasonlító PDF-et szeretnél, mint a heti riport, de saját dátumintervallummal:

```bash
./run_manual_report_and_sync.sh --report-type period_compare --start-date 2026-04-01 --end-date 2026-04-11
```

Ez a megadott időszakot az ugyanilyen hosszú, közvetlenül megelőző időszakkal hasonlítja össze, és készít:
- `period_funnel_report.csv`
- `period_funnel_report.html`
- `period_funnel_report.pdf`

- Kézi heti összehasonlító riport:

```bash
./run_manual_report_and_sync.sh --report-type weekly_compare
```

- Kézi havi összehasonlító riport:

```bash
./run_manual_report_and_sync.sh --report-type monthly_compare
```

- Kézi email küldés futás végén:

```bash
./run_manual_report_and_sync.sh --report-type period_compare --start-date 2026-04-01 --end-date 2026-04-11 --send-email
./run_manual_report_and_sync.sh --report-type monthly_compare --send-email
```

## Email beÃ¡llÃ­tÃ¡s

Az email kÃ¼ldÃ©s SMTP-vel mÅ±kÃ¶dik, Ã©s a standard libraryt hasznÃ¡lja, kÃ¼lÃ¶n csomag nem kell hozzÃ¡.
A PDF exporthoz a `reportlab` csomag kell, ez a `requirements.txt` rÃ©sze.

SzÃ¼ksÃ©ges env vÃ¡ltozÃ³k:

- `REPORT_SMTP_HOST`
- `REPORT_SMTP_PORT`
- `REPORT_SMTP_USERNAME`
- `REPORT_SMTP_PASSWORD`
- `REPORT_SMTP_USE_TLS`
- `REPORT_FROM_EMAIL`
- `REPORT_TO_EMAILS`

OpcionÃ¡lis automata kapcsolÃ³:

- `REPORT_AUTO_SEND_TYPES=weekly_compare,monthly_compare`

OpcionÃ¡lis user nÃ©v mapping a heti meeting bontÃ¡shoz:

- `GHL_USER_LABELS=userId1:NÃ©v 1,userId2:NÃ©v 2`

Ez azÃ©rt hasznos, mert a jelenlegi tokennel a GHL user endpoint nincs engedÃ©lyezve, ezÃ©rt a heti user meeting riport alapbÃ³l `assignedUserId` alapjÃ¡n dolgozik. Ha beÃ¡llÃ­tasz mappinget, a riportban mÃ¡r nevek jelennek meg.

Ez azt jelenti, hogy:

- a hÃ©tfÅi heti riport automatikusan emailben is kimehet
- a hÃ³nap 15-i havi riport automatikusan emailben is kimehet

## MegjegyzÃ©sek

- A kÃ³d tudatosan egyszerÅ± Ã©s konzervatÃ­v. Ha egy adott GHL sub-accountban eltÃ©r a custom field vagy appointment payload formÃ¡tuma, a `ghl_client.py` az elsÅ hely, ahol finomhangolni Ã©rdemes.
- A `normalize_contact()` Ã©s a riportÃ©pÃ­tÅ elÅkÃ©szÃ­tve maradt kÃ©sÅbbi source vagy campaign szerinti bontÃ¡sra.
