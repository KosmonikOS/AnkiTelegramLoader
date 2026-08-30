# Telegram → Anki Vocabulary Pipeline — Implementation Plan

## Goal

Send English words to a Telegram bot throughout the day from anywhere. Once a day
(or a few times a day) the MacBook wakes itself from sleep — even with the lid
closed — drains the queued words, looks up definitions from a free dictionary,
writes new Anki notes directly into the collection database, triggers a sync to
AnkiWeb/mobile, and goes back to sleep. No manual steps, no paid hosting.

---

## Architecture

```
Telegram (server-side queue, 24h retention)
        │  getUpdates (long poll, one-shot)
        ▼
[1] Drain script  ──appends──▶  pending_words.jsonl   (local durable store)
        │  advances offset only after local write succeeds
        ▼
[2] Batch processor
        │  for each unprocessed word:
        │     dictionary lookup (fallback chain) ──▶ definition, POS, IPA
        │     if Anki.app running → quit it, wait for exit
        │     write note directly to collection.anki2 via `anki` pylib
        │     mark word as done in local state
        ▼
[3] Sync trigger
        │  launch Anki.app (sync-on-open enabled) → wait → quit again
        ▼
[4] Scheduler
        pmset scheduled wake (lid closed) + launchd StartCalendarInterval
        fires steps 1–3 as one script, on a fixed schedule, without you
        touching the laptop.
```

---

## Configuration — `.env`

Every machine-specific path, credential, and tunable value lives in one
`.env` file at the project root, never hardcoded in the scripts. A
`config.py` module loads it once (via `python-dotenv`) and every other
script imports from `config.py` — no script reads `os.environ` directly.

`.env.example` (committed, no real values) documents every key; `.env`
itself (gitignored) holds the real ones.

```dotenv
# --- Telegram ---
TELEGRAM_BOT_TOKEN=                      # from @BotFather
TELEGRAM_ALLOWED_CHAT_ID=                # your own chat id, so the bot ignores anyone else's messages

# --- Anki ---
ANKI_COLLECTION_PATH=/Users/<you>/Library/Application Support/Anki2/User 1/collection.anki2
ANKI_DECK_NAME=Vocabulary
ANKI_NOTE_TYPE=Basic
ANKI_CLOSE_TIMEOUT_SECONDS=15            # graceful quit before force-kill
ANKI_SYNC_WAIT_SECONDS=45                # heuristic window for sync-on-open to finish

# --- Dictionary lookup ---
DICTIONARY_PRIMARY_URL=https://en.wiktionary.org/api/rest_v1/page/definition/
DICTIONARY_FALLBACK_URL=https://api.dictionaryapi.dev/api/v2/entries/en/
LLM_FALLBACK_ENABLED=false               # off by default — costs money per call
LLM_API_KEY=                             # only required if the above is true

# --- Scheduling ---
WAKE_TIMES=08:00,20:00                   # comma-separated HH:MM, feeds both
                                          # pmset and the generated LaunchAgent

# --- Paths ---
PROJECT_DIR=/Users/<you>/vocab-pipeline
STATE_DIR=${PROJECT_DIR}/state           # pending_words.jsonl, offset file live here
LOG_DIR=${PROJECT_DIR}/logs
```

`config.py`:
```python
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_ALLOWED_CHAT_ID = os.environ["TELEGRAM_ALLOWED_CHAT_ID"]

ANKI_COLLECTION_PATH = Path(os.environ["ANKI_COLLECTION_PATH"])
ANKI_DECK_NAME = os.environ.get("ANKI_DECK_NAME", "Vocabulary")
ANKI_NOTE_TYPE = os.environ.get("ANKI_NOTE_TYPE", "Basic")
ANKI_CLOSE_TIMEOUT_SECONDS = int(os.environ.get("ANKI_CLOSE_TIMEOUT_SECONDS", 15))
ANKI_SYNC_WAIT_SECONDS = int(os.environ.get("ANKI_SYNC_WAIT_SECONDS", 45))

DICTIONARY_PRIMARY_URL = os.environ["DICTIONARY_PRIMARY_URL"]
DICTIONARY_FALLBACK_URL = os.environ["DICTIONARY_FALLBACK_URL"]
LLM_FALLBACK_ENABLED = os.environ.get("LLM_FALLBACK_ENABLED", "false").lower() == "true"
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

WAKE_TIMES = [t.strip() for t in os.environ["WAKE_TIMES"].split(",")]

PROJECT_DIR = Path(os.environ["PROJECT_DIR"])
STATE_DIR = Path(os.environ.get("STATE_DIR", PROJECT_DIR / "state"))
LOG_DIR = Path(os.environ.get("LOG_DIR", PROJECT_DIR / "logs"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
```

`TELEGRAM_ALLOWED_CHAT_ID` matters more than it looks: without it, anyone
who finds the bot's username could send words into your deck. Have the
drain script discard any update whose `chat.id` doesn't match.

---

## Code quality & tooling

**Docstrings:** every public function, class, and module gets a
Google-style docstring (`Args:`/`Returns:`/`Raises:` sections). No
exceptions for short-looking functions like `ensure_anki_closed()` — if it
takes an argument or can raise, document it.

**Type hints:** every function signature fully typed, checked with `mypy`
in strict mode — not just enough to pass, but genuinely typed (no blanket
`Any` to silence errors).

**`pyproject.toml`** is the single source of project configuration —
dependencies, ruff config, mypy config, and package metadata for
`pip install -e .`. No `requirements.txt`, no separate `setup.cfg`.

```toml
[project]
name = "vocab-pipeline"
version = "0.1.0"
description = "Telegram-to-Anki vocabulary pipeline with headless batch processing"
requires-python = ">=3.11"
dependencies = [
    "requests>=2.31",
    "python-dotenv>=1.0",
    "anki==<pin to match installed Anki.app version>",
]

[project.optional-dependencies]
dev = ["mypy>=1.8", "ruff>=0.4", "pytest>=8.0"]

[project.scripts]
vocab-drain = "vocab_pipeline.drain:main"
vocab-process = "vocab_pipeline.process:main"
vocab-batch = "vocab_pipeline.run_batch:main"
vocab-setup-schedule = "vocab_pipeline.generate_launchagent:main"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "D", "UP"]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.mypy]
python_version = "3.11"
strict = true
disallow_untyped_defs = true

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```

**Verification gate — run before considering any build-order step below
"done":**
```
ruff check .
ruff format --check .
mypy src/
```
Both must pass clean, not just "no errors that block execution." Fix
docstring and type gaps as they're introduced, step by step — not in a
cleanup pass at the end, which tends to skip the boring ones.

Installation becomes `pip install -e '.[dev]'` from the project root. This
also gives the `vocab-*` CLI commands (`vocab-drain`, `vocab-process`,
`vocab-batch`, `vocab-setup-schedule`), which the LaunchAgent plist and
your own manual testing should call instead of invoking raw script paths.

Internal imports use the package namespace —
`from vocab_pipeline import config`, not a bare `import config` — since
everything lives under `src/vocab_pipeline/` once packaged (see file
layout below).

---

## README.md

Claude Code should write and keep current a README covering:

1. **What this is** — one paragraph, the goal statement from the top of
   this plan.
2. **Setup** — `pip install -e '.[dev]'`, copy `.env.example` to `.env`
   and fill in real values (bot token from @BotFather, chat ID, Anki
   collection path, wake times).
3. **One-time manual Anki setup** — enable "Automatically sync on profile
   open and close" in Preferences → Sync (required for the sync-trigger
   step to work at all).
4. **Running it manually** — `vocab-drain`, `vocab-process`,
   `vocab-batch`, what each does, and when to run one instead of the
   others (e.g. `vocab-drain` alone to see what's queued without touching
   Anki).
5. **Scheduling** — `vocab-setup-schedule` and what it changes (`pmset`
   wake schedule + LaunchAgent plist), and how to change `WAKE_TIMES`
   later.
6. **Architecture** — a short version of this plan's diagram, not a
   copy-paste of the whole document; link back to this plan file (if kept
   in the repo) for the full design rationale.
7. **Logs & troubleshooting** — where `logs/run_log.txt` lives, what a
   `failed` entry in `pending_words.jsonl` means and how to retry it, how
   to confirm the LaunchAgent actually fired (`launchctl list | grep
   vocabpipeline`, or the `logs/launchd.*.log` files).
8. **Known limitations** — the 24h Telegram cap, pylib/Anki.app version
   drift, sync-trigger timing heuristic — same list as in this plan's
   final section, kept in sync if either document changes.

Write it once the pipeline works end-to-end (after build step 6 below),
not speculatively before the interfaces are settled — update it again
after scheduling is wired up, once the real CLI commands and `.env` keys
are final.

---

## Dictionary source — decision

**Primary: Wiktionary REST API**
`https://en.wiktionary.org/api/rest_v1/page/definition/{word}`
- Free, no API key, no meaningful rate limit for personal use.
- Backed by Wiktionary itself — the largest free crowd-sourced English lexicon
  available: covers slang, technical terms, phrasal verbs, inflected forms,
  and multiple senses per word. Larger coverage than Merriam-Webster's free
  tier or the static dataset behind dictionaryapi.dev.
- Returns structured JSON already split by language and part of speech — no
  wikitext parsing needed.

**Fallback: Free Dictionary API**
`https://api.dictionaryapi.dev/api/v2/entries/en/{word}`
- Free, no key. Also Wiktionary-derived, but simpler JSON and more reliably
  includes IPA transcription and an audio pronunciation URL, which the raw
  Wiktionary REST response sometimes omits.
- Use this when (a) Wiktionary REST returns nothing for a word, or (b) you
  want the phonetic/audio fields it provides more consistently.

**Optional final fallback (not free, off by default):** if a word isn't in
either dictionary (typo, invented compound, very new slang), the pipeline can
call an LLM to generate a definition instead of silently failing. Gated by
`LLM_FALLBACK_ENABLED` in `.env` — it costs money per call, unlike the two
sources above, so it stays off unless explicitly turned on.

**Lookup function contract:**
```python
def lookup_word(word: str) -> dict | None:
    # returns {"word": str, "pos": str, "definition": str,
    #          "ipa": str | None, "example": str | None, "source": str}
    # or None if not found in any source
```

---

## No-lost-messages design

Two separate risks, two separate mitigations:

1. **Telegram's own 24h server-side retention is a hard ceiling.** If the
   Mac never wakes (fully shut down, not just asleep — `pmset schedule wake`
   cannot wake a powered-off machine) for more than 24h, any words sent in
   that gap are gone before the script ever runs. Mitigation: schedule the
   wake job **at least twice a day** (e.g. every 8–12h) for margin, and
   never fully shut down the machine — sleep only.

2. **Everything downstream of receipt (dictionary API down, Anki write
   crash, script bug) must not lose words that Telegram already delivered.**
   Mitigation: split into two phases.
   - **Phase 1 (drain):** call `getUpdates`, append every new word — raw,
     unprocessed — to a local append-only file (`pending_words.jsonl`) with
     its Telegram `update_id` and timestamp. Only *after* that local write
     succeeds do we advance the `offset` we send back to `getUpdates` (which
     tells Telegram it's safe to drop those updates). This phase should be
     trivial and hard to fail.
   - **Phase 2 (process):** read `pending_words.jsonl` for entries not yet
     marked done, look up + write to Anki, mark done. If this phase crashes
     partway, nothing is lost — unprocessed words are still sitting in the
     local file and get retried next run. This also makes the pipeline
     idempotent: safe to re-run after a crash without duplicate cards.

State files needed (both under `config.STATE_DIR`):
- `pending_words.jsonl` — append-only log of every word ever received, with
  `update_id`, `word`, `received_at`, `status` (pending/done/failed), and
  `error` (if failed, so failures are inspectable, not silent).
- `telegram_offset.txt` — last confirmed-drained `update_id`.

---

## Forcing Anki closed before writing

The `anki` pylib and the Anki.app GUI must never touch `collection.anki2`
concurrently — risk of lock conflicts or corruption. Before Phase 2 writes
anything:

```python
import subprocess, time
import config

def ensure_anki_closed(timeout=config.ANKI_CLOSE_TIMEOUT_SECONDS):
    if subprocess.run(["pgrep", "-x", "Anki"], capture_output=True).returncode == 0:
        subprocess.run(["osascript", "-e", 'tell application "Anki" to quit'])
        waited = 0
        while subprocess.run(["pgrep", "-x", "Anki"], capture_output=True).returncode == 0:
            time.sleep(1)
            waited += 1
            if waited >= timeout:
                subprocess.run(["pkill", "-x", "Anki"])  # force-kill fallback
                break
```

Call this before opening `Collection(...)`. Don't skip the polite `osascript`
quit attempt — force-killing first risks the same corruption you're trying
to avoid if Anki was mid-write.

---

## Headless Anki write

```python
from anki.collection import Collection
import config

col = Collection(str(config.ANKI_COLLECTION_PATH))
deck_id = col.decks.id(config.ANKI_DECK_NAME)  # creates if missing
notetype = col.models.by_name(config.ANKI_NOTE_TYPE)  # or a custom note type with IPA/example fields

for entry in pending_and_defined_words:
    note = col.new_note(notetype)
    note["Front"] = entry["word"]
    note["Back"] = format_definition_html(entry)  # definition, IPA, example
    col.add_note(note, deck_id)

col.close()
```

**Version constraint:** the `anki` pip package version must match the
installed Anki.app version reasonably closely — the collection schema and
internal APIs change between releases. Pin the pip version to match your
Anki.app version, and re-check after any Anki.app auto-update.

---

## Sync to mobile

Reimplementing Anki's sync protocol headlessly is unnecessary complexity.
Simpler and more robust: use Anki's own built-in "sync on profile open/close"
feature.

**One-time manual setup:** in Anki.app → Preferences → Sync → enable
"Automatically sync on profile open and close."

**Then, after Phase 2 closes the collection cleanly:**
```python
import subprocess, time
import config

subprocess.run(["open", "-a", "Anki"])
time.sleep(config.ANKI_SYNC_WAIT_SECONDS)
subprocess.run(["osascript", "-e", 'tell application "Anki" to quit'])
```
This lets Anki's own sync client do the real work instead of us re-deriving
the sync protocol. `ANKI_SYNC_WAIT_SECONDS` (default 45s) is a starting
guess for a small vocab deck, tunable in `.env` — raise it if logs show
Anki quitting mid-sync.

---

## Scheduling

> **Correction (post-implementation):** the `pmset repeat wake` loop shown
> below does not actually schedule multiple daily wake times — macOS's
> `pmset repeat` mechanism holds only one recurring wake event, so each
> iteration of the loop silently overwrites the previous one, leaving only
> the last `WAKE_TIMES` entry actually able to wake the Mac from sleep.
> The shipped implementation (`generate_launchagent.schedule_wake_events`)
> uses `pmset schedule wake` instead — a set of one-time wake events, which
> *can* hold one entry per configured time — and has `vocab-batch` re-arm
> the next occurrence of every time as its first step on each run, so the
> schedule keeps renewing itself. See the README's Scheduling section for
> the full mechanism and its failure mode.

`WAKE_TIMES` in `.env` is the single source of truth for when this runs —
nothing time-related is hardcoded elsewhere. A small setup script,
`generate_launchagent.py`, reads it and produces both the `pmset` wake
schedule and the LaunchAgent plist, so changing the schedule is a one-line
`.env` edit followed by re-running that script — never hand-editing plist
XML or `pmset` commands directly.

```python
# generate_launchagent.py
import subprocess
import config

# 1. Wake schedule — works with lid closed, no external display needed
for t in config.WAKE_TIMES:
    subprocess.run(["sudo", "pmset", "repeat", "wake", "MTWRFSU", f"{t}:00"])

# 2. LaunchAgent plist — one StartCalendarInterval entry per wake time
intervals = []
for t in config.WAKE_TIMES:
    hour, minute = t.split(":")
    intervals.append(f"""<dict>
        <key>Hour</key><integer>{int(hour)}</integer>
        <key>Minute</key><integer>{int(minute)}</integer>
    </dict>""")

plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.user.vocabpipeline</string>
    <key>ProgramArguments</key>
        <array><string>{config.PROJECT_DIR}/run_batch.py</string></array>
    <key>StartCalendarInterval</key><array>{''.join(intervals)}</array>
    <key>StandardOutPath</key><string>{config.LOG_DIR}/launchd.out.log</string>
    <key>StandardErrorPath</key><string>{config.LOG_DIR}/launchd.err.log</string>
</dict></plist>"""

path = f"{config.PROJECT_DIR.home()}/Library/LaunchAgents/com.user.vocabpipeline.plist"
open(path, "w").write(plist)
subprocess.run(["launchctl", "load", path])
```

Run-once note: a LaunchAgent (not LaunchDaemon) is required because it
needs the logged-in GUI session to launch Anki.app in the sync step. This
also means staying logged in (screen can lock, session must stay live) and
never fully powering off — sleep only, per the 24h Telegram cap above.

---

## File layout

```
~/vocab-pipeline/
├── pyproject.toml
├── README.md
├── .env                                  # gitignored — real values
├── .env.example                          # committed — documents every key, no secrets
├── .gitignore                            # .env, state/, logs/, .venv/
├── src/
│   └── vocab_pipeline/
│       ├── __init__.py
│       ├── config.py
│       ├── drain.py                      # Phase 1
│       ├── lookup.py                     # dictionary fallback chain
│       ├── process.py                    # Phase 2: define + write to Anki
│       ├── anki_guard.py                  # ensure_anki_closed()
│       ├── sync_trigger.py               # open/wait/quit Anki
│       ├── run_batch.py                   # orchestrates 1→4, called by launchd
│       └── generate_launchagent.py       # reads WAKE_TIMES, writes pmset + plist
├── tests/
│   ├── test_lookup.py
│   ├── test_drain.py
│   └── test_anki_guard.py
├── state/
│   ├── pending_words.jsonl               # durable local queue, see above
│   └── telegram_offset.txt
└── logs/
    ├── run_log.txt                       # timestamped summary per run
    ├── launchd.out.log
    └── launchd.err.log
```

---

## Build order (for Claude Code)

0. **Project scaffolding:** create `pyproject.toml`, `src/vocab_pipeline/`
   package with `__init__.py`, `.gitignore`, `.env.example` with every key
   documented, and `config.py` that loads and validates them (fail fast
   with a clear error if a required key is missing, rather than a
   confusing downstream crash). `pip install -e '.[dev]'`. Copy
   `.env.example` → `.env` and fill in real values (bot token, collection
   path, chat id) before writing anything else. Run `ruff check .` and
   `mypy src/` now, on the empty-ish package, so both are configured
   correctly before real code arrives.
1. **Telegram bot:** register via @BotFather, put the token in `.env`.
   Write `drain.py` — call `getUpdates`, discard updates not matching
   `TELEGRAM_ALLOWED_CHAT_ID`, write new entries to `pending_words.jsonl`,
   advance `telegram_offset.txt` only after the file write is confirmed.
   Google-style docstrings and full type hints on every function as it's
   written, not after. Test with `vocab-drain` by sending a few words and
   running it manually. `ruff check . && mypy src/` before moving on.
2. **Dictionary lookup:** write `lookup.py` implementing the Wiktionary
   REST → Free Dictionary API fallback chain, reading both URLs from
   `config.py`. Add `tests/test_lookup.py` covering a handful of words,
   including at least one that only the fallback should resolve. Lint +
   type-check gate again.
3. **Anki headless write, in isolation:** write `anki_guard.py` and a
   minimal script that quits Anki if open, adds one hardcoded test note via
   the `anki` pylib (using `config.ANKI_COLLECTION_PATH`), and closes
   cleanly. Verify in the GUI afterward that the note appears and nothing
   is corrupted, **before** wiring in real data. `tests/test_anki_guard.py`
   for the quit/timeout logic. Lint + type-check gate.
4. **Wire Phase 2 (`process.py`):** read pending words, call `lookup.py`,
   call the guarded Anki write, mark each word done/failed in
   `pending_words.jsonl` with error detail on failure. Lint + type-check
   gate.
5. **Sync trigger:** write `sync_trigger.py`, test manually via
   `vocab-process` + a manual sync-trigger run — confirm the phone's Anki
   app receives the new cards. Adjust `ANKI_SYNC_WAIT_SECONDS` in `.env` if
   it quits too early. Lint + type-check gate.
6. **Master script (`run_batch.py`):** chain drain → process → sync
   trigger → write a run summary to `logs/run_log.txt`. Run `vocab-batch`
   manually end-to-end several times before scheduling anything. Full
   `ruff check . && ruff format --check . && mypy src/` gate — this is the
   point the whole pipeline is functionally complete.
7. **Write the README** (see contents above) now that the interfaces and
   `.env` keys are settled.
8. **Scheduling:** run `vocab-setup-schedule` (`generate_launchagent.py`)
   — it reads `WAKE_TIMES` from `.env` and sets up both `pmset repeat
   wake` and the LaunchAgent plist in one step. Verify with a wake time a
   few minutes in the future — confirm via `logs/run_log.txt` that it
   actually fired unattended with the lid closed. Changing the schedule
   later is editing `WAKE_TIMES` and re-running this command. Update the
   README's scheduling section if anything here diverged from what was
   drafted in step 7.
9. **Soak test:** let it run on the real schedule for a few days before
   trusting it fully. Check `logs/run_log.txt` and `pending_words.jsonl`
   for any `failed` entries or gaps.

---

## Known limitations (accept these, don't try to engineer around them)

- **Telegram's 24h cap is absolute.** If the Mac is fully shut down (not
  sleeping) for over a day, words sent in that window are unrecoverable —
  this isn't a bug in the pipeline, it's Telegram's server-side retention
  limit. Twice-daily wake gives margin but doesn't eliminate the ceiling.
- **pylib/Anki.app version drift.** Anki.app auto-updates; the `anki` pip
  package needs to be re-matched after major updates or the collection
  write may fail outright (safer than corrupting silently, but still a
  maintenance point to watch in `run_log.txt`).
- **Sync-trigger timing is a heuristic (45s), not a completion signal.**
  For a small personal vocab deck this is generous margin, but it's worth
  glancing at logs occasionally rather than assuming it's always fine.
- **No real-time confirmation.** Since there's no persistent listener,
  you won't get an instant "✓ added" reply in Telegram — confirmation
  only happens at the next scheduled batch run. If that matters, it needs
  a live component, contradicting the "no paid host" and "batch is fine"
  constraints, so it's not included here.
