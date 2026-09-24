# Job Board — your own AI job search

Every morning it finds jobs that fit **your** resume, reads each one for you, and gives you a quick gut check:
worth applying or not, why, what they'll expect, and what to ask for. Swipe right to apply and it opens the posting
and logs it in your tracker.

**Free. Private. Yours.** It runs on GitHub's free plan in your own account, and everything personal is
encrypted with a passphrase only you know.

### → [Set up your own board](https://deveshpat.github.io/job-board/setup.html) (about 5 minutes)

You need a free GitHub account. The setup page makes your copy, turns on your website, and sets up encryption
from one GitHub key — no terminal, no installs.

---

## What you get

- **A daily search** across remote job boards, Hacker News "Who is hiring", and the careers pages of about 30
  AI and tech companies (add your own) — only postings that fit your field, level and location reach you.
- **A gut check on every job** — the verdict, the points for and against, and the posting's own words on
  location, experience and pay, so you can check the call in seconds.
- **The full description, made readable** — requirements highlighted, boilerplate folded away, your skills and
  missing skills marked.
- **What to ask for** — the salary they list, or a clearly labelled estimate for your country and level.
- **A tracker** — every application, status, follow-up date and note, sortable, exportable to CSV.
- **A resume editor** — edit your resume like a document; it's typeset in LaTeX for you (or edit the LaTeX
  directly), and your profile updates from it.
- **Filters that matter** — jobs in languages you don't work in, roles far above your level, and postings
  that won't hire where you live stay off your deck.

## Private by design

- Your resume, profile, swipes and tracker are encrypted (AES-256-GCM) in your own GitHub repository.
  The repository is public so GitHub Pages is free, but it holds only ciphertext.
- Unlock with your passphrase, or a passkey (Face ID / Touch ID). Nobody else — including the author of this
  project — can read your data or recover your passphrase.
- Keys you add (Kaggle, TypeSafe) go into your repository's secrets and can't be read back.

## How jobs are judged

Each posting is read by a "System One" decision model that answers typed questions — *is this open to someone
in your country? what level is it? how well do your skills cover it?* — with probabilities, and code turns the
answers into a match score and hard filters. You choose the engine during setup:

- **Free:** your own free Kaggle account runs an open model ([Kev](https://github.com/jaredpalmer/kev)) on
  Kaggle's GPUs.
- **Faster:** a [TypeSafe](https://typesafe.ai) API key (you pay TypeSafe directly — typically cents a day).
- **Neither yet:** a basic keyword match until you add one.

How we measured the engines, the questions they answer and the scoring are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Where the jobs come from

Job data from [Remotive](https://remotive.com), [Himalayas](https://himalayas.app), [Jobicy](https://jobicy.com),
[Arbeitnow](https://www.arbeitnow.com), [Hacker News](https://news.ycombinator.com) and
[We Work Remotely](https://weworkremotely.com) (opt-in), and companies' own job boards on
[Greenhouse](https://www.greenhouse.com), [Ashby](https://www.ashbyhq.com) and [Lever](https://www.lever.co).
Every job links to its original posting and shows where it came from. Your copy fetches from these public APIs
for your own private use — please respect each source's terms, and don't republish their listings.
LinkedIn, Indeed and similar sites aren't included: their terms forbid automated access.

## Run it on your own computer (developers)

The same app runs locally with a FastAPI backend (Python 3.9+):

```bash
cp .env.example .env      # choose an engine
./run.sh                  # → http://localhost:8000
```

`../.venv/bin/python -m pytest -q tests` runs the test suite offline. Architecture, benchmarks and the full
question list: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
