# Stealth

Free, open-source personal data removal for people in the UK. Stealth writes to the companies that
sell your data, tells them to delete it, keeps track of who answered, and helps you escalate to the
Information Commissioner's Office (ICO) when they don't. It runs on your own computer. There is no
account, no server and no telemetry.

**Status: Phase 1 of 4.** The engine and command line work today. The desktop app, setup wizard,
Google and Microsoft sign-in and installers are Phase 2, so for now you need Python and a terminal.

## What works today

- Letters citing UK GDPR Articles 17, 19, 21, 12(3) and 12A, stating the exact reply-by date
- Preview by default: nothing is sent until you add `--live`
- A daily sending limit and pacing, so your mailbox isn't flagged for bulk sending
- Reply detection that only reads your mailbox and never changes it
- Bounce detection, which flags dead contacts
- A formal complaint under section 164A of the Data Protection Act 2018 once the deadline passes
- A drafted ICO complaint when a company has had its chance
- Repeat requests after 60 days for people-search sites and 90 days for others, because brokers
  re-list people
- An encrypted database, with keys in your operating system's keychain
- A tamper-evident audit log: every email sent and received is kept with a hash
- A UK checklist of steps only you can do
- `stealth demo`, a full fake cycle that sends no real email

## How many companies

Stealth counts distinct contacts rather than list entries, because many sites share one privacy
inbox.

| | Contacts |
|---|---|
| Written to by default for a UK user | **75** (66 by email, 9 by web form) |
| Optional, off by default | 517 |

These are the numbers from the current list. Run `stealth brokers` to see them for your copy.

The default list is smaller than the headline numbers you'll see elsewhere, on purpose. Most of the
optional companies are US firms from an auto-imported California register. UK law only reaches them
if they target or monitor people in the UK, and their entries are less reliable. Opting in sends
your name and email to them, and nothing more.

## Stealth compared with Incogni

Based on Incogni's public plans. Stealth is an independent project and isn't connected to Incogni.

| | Incogni | Stealth |
|---|---|---|
| Price | Paid subscription | Free |
| Where your data lives | Their servers | Your computer |
| Automated requests | 420+ brokers | 75 by default for UK users, 517 optional |
| Repeat requests | Yes | Yes |
| Evidence you can export | Limited | Every email, with hashes |
| ICO escalation drafted for you | No | Yes |
| Can you check the broker list? | No | Yes, and you can fix it |
| Handles replies for you | Yes | Partly: it matches replies, and you decide what they mean |
| Custom removals | Unlimited plans | Phase 2 |
| Dark web monitoring | Protect plan | Phase 3, breach monitoring only |
| Identity theft insurance | Protect plan | **No, and it never will** |
| Credit monitoring | Protect plan | **No.** Stealth reminds you to check your free reports |
| Human agents and phone support | Yes | **No** |
| Mobile app | Yes | Phase 4 plan |

If you want someone else to handle all of this and don't mind paying, Incogni covers more companies
and does more of the work for you.

## Limitations

- **No tool gets 100%.** Some companies ignore requests, and some will ask for ID.
- **Leaked data can't be recalled.** Removal stops future sales, not copies already passed on.
- **Brokers re-list people**, which is why requests repeat. Very frequent repeats could be called
  excessive under Article 12(5), so the intervals can be changed.
- **Your data does leave your computer when you choose to send it.** Each letter goes to the company
  it's addressed to, through your email provider.
- **Some contacts will be out of date.** Bounces are flagged so they can be fixed.
- **This isn't legal advice.** Check anything you send.

## Try it

You need Python 3.11 or later.

```
git clone https://github.com/Anish25012/stealth
cd stealth
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e .
stealth demo
```

`stealth demo` runs a whole story with fake companies against a mail server on your own machine,
fast-forwarding through three months.

## Using it for real

**1. Create a Gmail app password.** Turn on 2-Step Verification on your Google account, then create
an app password at <https://myaccount.google.com/apppasswords>. Your normal password won't work.
Outlook and Hotmail no longer allow app passwords, so those accounts need the Microsoft sign-in
arriving in Phase 2.

**2. Set up your profile.**

```
stealth init
```

Write down the recovery code it shows you. It's the only way back in if your keychain is lost.

**3. Preview your letters.** These are written to your outbox folder and nothing is sent.

```
stealth send
```

**4. Send a test to yourself first.** See [Testing safely](#testing-safely).

**5. Send.**

```
stealth send --live
```

Stealth sends up to 40 a day, 20 seconds apart. Run it again on later days, or schedule it, to get
through the rest.

**6. Keep it going.**

| Command | What it does |
|---|---|
| `stealth cycle --live` | Does everything due. This is the one to schedule |
| `stealth status` | Shows every case. Add `--csv` to export |
| `stealth inbox` | Checks for replies |
| `stealth confirm <company> removed` | Records what a reply meant |
| `stealth forms` | Lists companies that need a web form |
| `stealth forms --done <company>` | Starts the clock once you've sent a form |
| `stealth complain <company>` | Sends a formal complaint now, after a refusal |
| `stealth ico <company>` | Drafts an ICO complaint |
| `stealth checklist` | Lists the UK steps only you can do |

Only you can mark a company as finished. Stealth marks a case `replied`, and `stealth confirm`
records whether that reply was `removed`, `no_data`, `refused`, `id_requested` or `extended`. When
you send the ID a company reasonably asked for, use `info_supplied`: UK law restarts the time limit
from that point, and Stealth moves the date to match.

### Testing safely

To check your real mailbox works before writing to anyone, add your own address as a broker and
send to it alone with `stealth send --live --only me-test`. See
[docs/testing-with-your-own-inbox.md](docs/testing-with-your-own-inbox.md).

### How the timing works

| Stage | When |
|---|---|
| Reply due | One calendar month after sending, clamped to the end of the month |
| Formal complaint | The day after that, if there's been no reply |
| ICO-ready | 30 days after the complaint, because section 164A gives companies 30 days just to acknowledge it, or immediately if they refuse |
| Repeat request | 60 days for people-search sites, 90 for others |

You can change the repeat intervals and the ICO wait.

## Scheduling

**Windows Task Scheduler.** Run this once in a terminal, using the full path to `stealth.exe`:

```
schtasks /Create /SC DAILY /ST 09:00 /TN "Stealth" /TR "C:\path\to\stealth\.venv\Scripts\stealth.exe cycle --live"
```

**macOS and Linux cron.** Run `crontab -e` and add:

```
0 9 * * * /path/to/stealth/.venv/bin/stealth cycle --live
```

Scheduled runs need your keychain to be unlocked, so on a headless Linux machine the keyring needs
extra setup.

## Where your data lives

| | |
|---|---|
| Windows | `%LOCALAPPDATA%\Stealth` |
| macOS | `~/Library/Application Support/Stealth` |
| Linux | `~/.local/share/Stealth` |

Set `STEALTH_HOME` to use another folder. Each profile has its own encrypted database, and logs are
stripped of personal data as they're written.

## Fixing the broker list

The list is open, and fixes are welcome.

- **A contact is wrong?** [Open an issue](https://github.com/Anish25012/stealth/issues) with the
  company name and where you found the right address. Don't include your own details.
- **Want to fix it yourself?** UK contacts live in `tools/seed_uk.json`. Run
  `python tools/build_brokers.py` afterwards to regenerate the list.
- **Just for you?** Add entries to `brokers_extra.json` in your data folder. It uses the same format
  as `src/stealth/registry/data/brokers.json` and overrides by `id`.

## Development

```
pip install -e ".[dev]"
pytest --cov
ruff check .
ruff format --check .
mypy
```

The tests run real SMTP and IMAP code against servers on `127.0.0.1`, and move a controllable clock
through whole case lifecycles.

| Package | Purpose |
|---|---|
| `src/stealth` | The engine: all logic, with no UI code |
| `src/stealth_cli` | The command line, which only prompts and prints |
| `tools/build_brokers.py` | Builds the broker list |

## Roadmap

1. **Core engine and command line** (done)
2. **Desktop app:** installers, setup wizard, Google and Microsoft sign-in, dashboard, reply
   classifier, custom removals, assisted people-search scan, reports, other jurisdictions
3. **Protect equivalents:** breach monitoring, credit report reminders, Cifas tracking, recovery
   playbook
4. **Planned only:** form pre-fill and a mobile app

## Licence

MIT. See [LICENSE](LICENSE). The broker list builds on the MIT-licensed
[DataPurge](https://github.com/puurpl/datapurge) registry: see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
