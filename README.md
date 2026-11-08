# Pay Ladder: live UK job & apprenticeship finder

Finds live UK vacancies in trading, investing, investment banking, corporate finance,
transaction services and private equity, then ranks them for **your** profile
(`profile.json`) by how much they pay and how realistic they are for you right now.

## What it searches

Keyless, every run:

| Source | Employers |
|---|---|
| Greenhouse API | Jane Street, IMC, Jump, DRW, HRT, XTX, Tower, Akuna, Flow Traders, Virtu, Squarepoint, Point72, Schonfeld, AQR, Man Group, Winton, General Atlantic, Lincoln International, William Blair, Teneo, Monzo, Wise, Ebury, GoCardless |
| Workday API | Morgan Stanley, Citi, Barclays, Deutsche Bank, Bank of America, Wells Fargo, MUFG, Mizuho, BMO, TD, ING, Santander, Lloyds, Blackstone, Carlyle, Apollo, ICG, Houlihan Lokey, PwC, Grant Thornton UK, RSM UK, State Street, Neuberger Berman, LSEG |
| Oracle API | J.P. Morgan, Lazard |
| SmartRecruiters API | FRP Advisory, Evelyn Partners |
| EY careers site | EY Strategy & Transactions, Parthenon, Assurance |
| Ashby API | Trading 212 |
| Workable API | Interpath Advisory |
| Eightfold API | HSBC |
| Oleeo (tal.net) feeds | Evercore, Jefferies (campus) |
| Own careers sites | KPMG UK (kpmgcareers.co.uk), BDO UK (Radancy), Deloitte UK (Avature), Goldman Sachs (higher.gs.com), Rothschild & Co |
| GOV.UK Find an apprenticeship | every finance / accounting / banking apprenticeship, highest wage first |

Deloitte UK's server rejects older TLS libraries (for example the LibreSSL in macOS's built-in
Python 3.9), so it only answers from a newer Python such as the one GitHub Actions uses. It shows as an
error in local runs; the Sources line on the page shows whether it answered in the daily refresh.

Optional free keys open up the **whole UK market** (recruiters, smaller advisory firms and
everyone not listed above), with advertised salaries:

| Env var | Get one at |
|---|---|
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | https://developer.adzuna.com |
| `REED_API_KEY` | https://www.reed.co.uk/developers |
| `FAA_API_KEY` | https://developer.apprenticeships.education.gov.uk |

## Run it

```bash

python3 -m finder.run            # writes data/jobs.json + data/report.md, prints the top 25
python3 -m finder.run --top 50
python3 -m finder.run --probe    # check every employer feed still answers
```

Python 3.9+ only, no packages to install. A full run takes about 40 seconds.

Then open `index.html` (it reads `data/jobs.json`). To serve it locally:
`python3 -m http.server` and go to http://localhost:8000.

`.github/workflows/job-finder.yml` reruns it every morning and commits fresh results.
Add the optional keys as repository secrets to use them there.

## How the ranking works

```
match = pay × reach × (0.25 + 0.75 × interest fit) × work-from-home bonus
```

- **Pay**: total compensation (base plus typical bonus). If the advert states a salary it is used;
  otherwise it is estimated from employer type (prop shop, bulge bracket, boutique, megafund PE,
  Big 4, ...) and seniority, using the bands in `finder/scoring.py`. Edit them if you know better.
- **Reach**: how realistic the role is for you today. It starts from seniority and is adjusted from
  the advert text: lower if it asks for a degree, a fully qualified accountant, a student, GCSE grades or
  UCAS points above yours, quant maths, or associate-level IB experience; higher if it asks for ACA,
  AAT, Big 4 or audit background, and for audit → transaction services moves.
- **Interest fit**: matches against your six target areas, with ACA-friendly stepping stones
  (fund finance, product control, valuations, corporate development) at about half weight.
- **Work from home**: remote ×1.15, hybrid ×1.06, explicit office-only ×0.97 (`profile.json`).

Every role carries plain-English flags explaining its reach score, for example
*"Wants a qualified accountant - open to you once ACA is complete"*.

## Adding employers

Copy a line in `sources.json`. Greenhouse, Lever, Ashby and SmartRecruiters need only the
board slug; Workday needs host, tenant and site from the careers URL
(`https://<host>/<site>/...`). Run `--probe` to confirm it answers.
