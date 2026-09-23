# Job Board: resume-matched jobs, decided by a System One model

A personal job board. It scrapes public job feeds and scores every posting against your resume
with a "System One" decision model: by default **[Kev-4B](https://github.com/jaredpalmer/kev)** running
locally on this Mac (free), or TypeSafe's hosted **[Jev](https://docs.typesafe.ai)**, which speaks the same
API. Only the relevant ones reach a swipe deck. Swipe right to apply (the posting opens and a row is added to
the tracker), left to pass, or up to save for later.

```
./run.sh                 # → http://localhost:8000
```

1. `./run.sh` starts the app. The engine is chosen in `.env` (copied from `.env.example`); to use hosted
   Jev instead, follow the comments there. The local Kev-4B engine (~8 GB) is **loaded on demand** — only
   during pipeline runs and profile builds, unloaded after 5 idle minutes (`app/engine.py`).
2. **Profile**: click *Use Devesh_Patel_Resume.tex* (or upload a PDF/TeX). The engine picks your
   keywords, field, level, country and the job titles to search for. Every pick is a toggle you can override.
3. **Profile → Run pipeline**.
4. **Applications**: swipe. **Tracker**: edit status, dates and notes inline; add jobs you applied to
   elsewhere (paste the link and company/role fill in; if it's a job on your board, it's linked and leaves
   the deck); export to CSV.

**Daily routine.** While the app is open it runs the pipeline every day at *Daily run at* (Profile page,
default 09:00) and catches up on start if today's run was missed. Each run only processes new postings.
`../.venv/bin/python scripts/daily.py` runs it once from a terminal (using the open app if there is one).
(A macOS launchd schedule isn't used: background jobs can't read `~/Documents` without Full Disk Access.)

**Tracker backup.** Every tracker change rewrites `../tracker_backup.csv` (next to your resume). Keep that
file when reformatting; restore with `../.venv/bin/python scripts/import_applications.py ../tracker_backup.csv`.
The full database (swipe history, labels) is `data/jobboard.db`.

With no engine configured the app still runs, using a plain keyword-overlap heuristic that is clearly
labelled on every card.

## Web app on GitHub Pages (runs even when your Mac is off)

Profile → **Publish to GitHub** in the Mac app does the whole setup:

1. Create an **empty public repo** on GitHub (e.g. `job-board`).
2. Create a **fine-grained token** (GitHub → Settings → Developer settings → Fine-grained tokens) for
   *only that repo*, with **Read and write** on Contents, Actions, Secrets, Workflows and Pages.
3. In the app enter `your-username/job-board`, the token and a passphrase of **5+ random words**, then Publish.

It uploads the app to `main`, creates an orphan `data` branch holding your data **encrypted**
(`keys.json`, `user.enc`, `pipeline.enc`, `board.enc`), saves `JOBBOARD_DATA_KEY` and `KAGGLE_KEY_<USER>`
as Actions secrets and turns on Pages. The web app is then at `https://your-username.github.io/job-board/`.

- **Unlock** in any browser with the passphrase, or add a passkey (Face ID / Touch ID) from Profile → Sync &
  security. Each browser keeps an encrypted copy in IndexedDB (marked persistent) and syncs changes back;
  edits from different devices merge record by record. Add your token there too to save changes.
- **Daily runs** happen on GitHub Actions (`.github/workflows/daily.yml`, hourly trigger that only acts at
  your chosen time): scrape → Kev on Kaggle (GPU T4 ×2 across your accounts, then Kaggle CPU) → encrypted
  results committed to `data`. If Kaggle is unavailable that day, it's skipped and retried the next day.
- **The repo can be public**: everything personal is AES-256-GCM ciphertext; keys live in Actions secrets.
  Someone else wanting their own board uses "Use this template" — your repo never holds their data.
- The Mac app stays fully usable. While GitHub sync is on it syncs every 5 min (and after each change), and
  its *Run pipeline* button starts the GitHub run instead of a local one.
- If the site doesn't appear within a few minutes of publishing, re-run the *Pages* workflow from the
  repo's Actions tab.

## Which engine, and why (see `bench/`)

`bench/` holds a frozen test set (76 real postings + 200 titles, hand-labelled against the resume) and a
harness that runs any `/v1/systemone` server over it. Results on an M4 / 16 GB, 13 core questions per job:

| | Decider-2B | **Kev-4B** | Kev-0.8B | open-jev (Gemma 3 4B) |
|---|---|---|---|---|
| technical role? / level / years asked | 92% / 95% / 86% | **99% / 98% / 95%** | 64% / 88% / 38% | 59% / 10% / 44% (32 jobs) |
| open to India? | 81% | **86%** | 40% | 45% |
| skill-match error (0–4) | 1.67 | **0.49** | 1.43 | 2.14 |
| board at min match 40 (4 good jobs) | 4 kept, 11 wrong | **4 kept, 1 wrong** | 4 kept, 23 wrong | — |
| seconds per job / per 60 titles | 15 / 56 | 18 / 71 | 2.6 / 6 | 97 / 128 |

Re-run: start an engine, then `../.venv/bin/python bench/run.py --name X --base-url http://127.0.0.1:PORT`
and `../.venv/bin/python bench/report.py`. Engines are installed under `bench/engines/` (git-ignored).
Caveats: only 4 positives in the set, and the labels are one person's judgment.

## Hosted Jev (optional)

Jev takes a *state* plus typed questions (`noul` = yes/no probability, `choice`, `score`) and returns
calibrated answers in about 100 ms. That's exactly the shape of "is this job relevant to me?". Pricing is
$0.042 per 1M input tokens (output is free). By my estimate the first full run over ~6.7k postings is about
5M tokens (~$0.20); later runs only touch new postings and cost a few cents. It's pay-per-use, not a subscription.
To swap in a self-hosted, Jev-compatible server, set `JEV_BASE_URL` (and `JEV_MODEL`) in `.env`. Answers are cached in `data/jev_cache.db`, so re-runs don't pay twice.

## The ETL pipeline

| Stage | What happens | Who decides |
|---|---|---|
| **Profile** | Resume → keyword/project/location candidates | Code extracts, Jev keeps/drops |
| **Extract** | Remotive, Himalayas, Jobicy (searched with Jev's terms) · We Work Remotely, Arbeitnow, HN "Who is hiring" · Greenhouse/Ashby/Lever boards of ~30 AI and India-hiring companies | Code |
| **Filter** | Dedupe, drop postings older than *max age*, skip ones already seen | Code (dates and arithmetic stay in code, per TypeSafe's guidance) |
| **Triage** | Every title is checked: *in your field? above your level?* (60 titles per request) | Jev |
| **Evaluate** | Full posting + compact profile → ~13 judgments + per-skill checks | Jev |
| **Score** | Weighted composite (0–100) + hard gates + confidence flag | Code, from Jev's probabilities |
| **Load** | Jobs above *min match* go to the board; the rest are kept as `filtered` with the reason | Code |

Changing *min match* re-scores from stored answers instantly, with no new Jev calls.

### The questions Jev answers (all in [`app/questions.py`](app/questions.py))

**About you (state = your resume)**
- For each extracted keyword: *does the resume show you actually using it?* and *do employers commonly require it?* → decides which keywords are kept and which are "core"
- *Which field are you best positioned for?* (11 options; the probabilities become your field mix)
- *What level fits your paid experience?* (internship / entry / mid / senior / staff+)
- *Which country do you live in?* (drives location eligibility)
- For 31 role titles: *would you be a competitive applicant?* → the top ones become search terms

**About each title (triage)**
- *Is this title in your field?* · *Does it name a level above yours?*

**About each posting (evaluation)**
- Is it a hands-on technical role? · Which field? · Which level?
- Could someone living in your country, with work authorization only there, be hired without moving? (remote-open / local office / restricted / unclear)
- Minimum experience asked (bucketed) · Degree requirement
- Skill coverage (0–4 rubric) · Fit with your stated goal (0–3 rubric)
- Red flags (scam, unpaid, commission-only, vague)
- Work mode · Employment type
- Which of your projects to lead the application with
- Holistic: *is this a realistic, worthwhile application for you?*
- For each of your kept skills: *does the posting ask for it?* → green chips
- For ~45 common skills you don't list: *does it require this?* → red "gap" chips

**Gates** (a posting failing any of these never reaches the board): not a technical role · non-engineering field ·
less than 40% likely to be open to your country · mostly above your level · asks for many more years ·
red flags · match below the threshold (default 40). The gates rely on the factual answers, which every
engine gets right far more often than the holistic skill/"should apply" judgments.

## Layout

```
app/
  jev.py        Jev HTTP client: retries, question chunking, response cache
  questions.py  every question, weight and threshold (the file to tune)
  profile.py    resume parsing (.tex/.pdf/.txt) + Jev profile
  sources.py    9 job-source adapters → one normalized schema
  pipeline.py   extract → triage → evaluate → score → load
  db.py         SQLite (data/jobboard.db)
  main.py       FastAPI JSON API + static UI
web/            index.html, styles.css, app.js (no build step)
tests/          end-to-end test against tests/fake_jev.py (a local stand-in for the API)
```

`../.venv/bin/python -m pytest -q tests` runs the tests offline, without spending credit.

## Notes

- LinkedIn, Indeed and Naukri aren't scraped: their terms forbid it and they block bots. Add companies you
  care about to the Greenhouse/Ashby/Lever lists instead (`DEFAULT_COMPANIES` in `app/sources.py`).
- Your key stays on the server (`.env`); the browser never sees it.
- If a type of card keeps coming out wrong, look at that job's stored answers (`eval` column in
  `data/jobboard.db`) and tighten the wording of the question in `questions.py`. Jev reads questions literally.
