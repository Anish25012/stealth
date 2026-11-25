# Stealth: notes for Claude Code

Stealth is a UK-first, local-only desktop app that sends data-removal requests (UK GDPR and others)
from the user's own email, tracks replies and follows up. Public MIT repo: Anish25012/stealth.
It's for the maintainer and their UK family and friends. No server, account or telemetry.

## Layout and stack

- `src/stealth`: the engine (all logic, no UI). SQLCipher database per profile, keys and mail
  sign-ins in the OS keychain via `keyring` (service `stealth-privacy`).
- `src/stealth_cli`: Typer command line (`stealth init`, `send`, `inbox`, `google`, ...).
- `src/stealth_app`: FastAPI + Jinja app on 127.0.0.1, shown in a pywebview window, with a tray.
- `packaging/`: PyInstaller spec, Inno Setup script, `build.py`. `site/`: the GitHub Pages site
  (home page and privacy policy, deployed by `.github/workflows/pages.yml`).
- Mail: Gmail via Google sign-in and the Gmail API (`mail/google_oauth.py`, `mail/gmail_api.py`),
  or SMTP/IMAP with an app password (`mail/smtp_imap.py`). Both implement `Transport`/`Mailbox`.

## Setting up a new computer

```
git clone https://github.com/Anish25012/stealth
cd stealth
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[app,dev]"
```

Then create `src/stealth/mail/google_secret.py` (git-ignored, never commit it):

```python
CLIENT_SECRET = "GOCSPX-..."  # noqa: S105
```

The secret is in Google Cloud › Google Auth Platform › Clients › "Stealth desktop", and in the
`GOOGLE_CLIENT_SECRET` Actions secret (write-only). Without it, Google sign-in is simply hidden.

## Checks (run before every commit)

```
pytest -W ignore            # ~600 tests; test_visual needs Edge or Chrome installed
ruff check .
ruff format --check .
mypy
```

CI (`.github/workflows/ci.yml`) runs these on Windows, macOS and Ubuntu, then builds installers.
A `v*` tag also makes a **draft** GitHub release; the maintainer publishes it by hand.

## Rules

- Work on a branch and open a pull request; don't push straight to `main` unless asked.
- Never commit personal data, `google_secret.py` or any key. `tests/test_repo.py` guards this
  and also fails on non-UTF-8 text files. On Windows, write files with `encoding="utf-8"`.
- Never ask for, print or handle the user's passwords. Read secrets from the keychain only inside
  scripts, never echo them.
- For commands on the real profile, use the clean worktree at
  `C:\Users\anish\projects\stealth-main-dryrun`; never the OneDrive copy.
- Real sends (`stealth send --live`) need the user's say-so and the right test address; use a
  separate `STEALTH_HOME` (for example `~/stealth-test`) for testing, never the real data folder.
- Tests must never reach real services: Google sign-in is off in tests unless a test fakes it
  (`tests/conftest.py`).
- Don't publish releases, change repo settings or contact anyone without asking.
- Write plain UK English in docs and UI text.

## Decisions already made (don't reopen)

- Broker tiers: a core list on by default; an extended list (mainly US) is opt-in and shares only
  name and email.
- Minimum details by company (`registry/details.py`): UK companies, marketing lists and public
  records get everything; non-UK contact scrapers and people search get name, email and phone;
  non-UK ad tech, location tracking and "other" get name and email. Letters keep the "don't add
  details you don't already hold" line.
- ICO complaint is ready 30 days after a formal complaint, or straight away after a refusal.
- People-search scan is assisted: the person runs the search, Stealth captures it.
- Autopilot (`stealth/autopilot.py`, setting `autopilot`): on for new profiles, off for profiles
  made before it (the app explains it once). On Autopilot the app's daily check sends first
  letters to core companies in daily batches within the limit, repeat requests, and complaints
  after a missed deadline or a refusal, logging each as an `autopilot` event ("Sent
  automatically"). It also opens "click to confirm your request" links, only under the rules in
  `inbox/verify.py` (company's own HTTPS domain, threaded or provider-vouched sender, one GET, page
  says confirmed, no CAPTCHA); anything else goes to Needs you. Follow-ups (`followups.py`),
  once per round and only for proven replies: Article 12(6) pushback on ID requests that name no
  documents, the first letter's details on "more details", a chase on opt-out-only answers
  (deadline unchanged). `autopilot_from` holds first letters and repeats until a date. Still the person's: ID documents, web forms/CAPTCHAs, deleting old accounts
  not marked Delete, replies Stealth can't read confidently, ICO complaints, extended-tier letters.
- With Autopilot off, approve-before-send applies in the desktop app; the CLI's
  `send --live --yes` counts as approval. Complaints wait too (Needs you, or
  `stealth complain <id>`), unless "Send complaints automatically" (`auto_complaints`) is on.
- macOS is untested and its window smoke check may fail (issue #1). Don't fix unless asked.
- Other countries (EU/EEA, Switzerland, California, Canada, Australia) get lighter, clearly
  labelled letters. Quebec isn't covered.
- Outlook/Hotmail aren't supported (Microsoft dropped app passwords). Gmail uses Google sign-in,
  with only the `gmail.send` and `gmail.readonly` scopes, as an unverified app under Google's
  personal-use (<100 users) exemption.
- Phase 4 (form pre-fill, mobile app) is plan-only: ask before building.

## Where things stand

- Phases 1, 2 and 3 are done (Phase 3: breach monitoring, credit report reminders, Cifas
  tracking, recovery playbook, find my old accounts). v0.3.0 is a draft release (not
  published); the old v0.1.0 and v0.2.0 drafts were deleted, but their git tags are kept.
- Google sign-in works (tested end to end: send, reply detected, case settled).
  `https://anish25012.github.io/` is verified in Google Search Console. To finish: save the
  Branding page, publish the app (no verification), sign in again, then publish v0.3.0.
- Not yet tried against the real services: HIBP breach checks. The Gmail sign-up scan has run
  on the maintainer's real inbox (183 companies found).
- Next: Phase 4 is plan-only (ask before building).

## Quirks seen on the maintainer's Windows PC

- Python sometimes crashes at random (access violations) outside Stealth code: rerun first.
- Norton intercepts HTTPS and re-signs IMAP/SMTP-over-TLS; Stealth uses `truststore` (Windows'
  store). Norton's email scanning blocks IMAP port 993; Google sign-in avoids it.
- Installers built locally there can fail with a hash mismatch; install CI-built ones instead.
- pytest warnings can hide the summary line; check the exit code.
- Tests run with a private temp folder; a test that leaves a `stealth-*` folder in it fails
  (`tests/conftest.py`). Delete temp folders with `paths.remove_folder`, never
  `ignore_errors=True`, so a folder that can't be removed is logged.
