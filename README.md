# AnkiTelegramLoader

Send English words to a Telegram bot throughout the day, from anywhere. A
few times a day your Mac wakes itself from sleep — even with the lid closed
— drains the queued words, looks up definitions from a free dictionary,
writes new Anki notes directly into your collection, triggers a sync to
AnkiWeb/mobile, and goes back to sleep. No manual steps, no paid hosting.

## Setup

1. Install the project in editable mode, with dev tools:

   ```bash
   pip install -e '.[dev]'
   ```

   This also installs the `anki` pip package, pinned in `pyproject.toml`.
   **Before installing**, edit that pin (`anki==...`) to match your
   installed Anki.app version exactly — check Anki → About Anki. The
   collection schema and pylib API can drift between releases, and a
   mismatched pin risks failed or incorrect writes.

2. Copy the example environment file and fill in real values:

   ```bash
   cp .env.example .env
   ```

   - `TELEGRAM_BOT_TOKEN` — create a bot via
     [@BotFather](https://t.me/BotFather) and paste the token it gives you.
   - `TELEGRAM_ALLOWED_CHAT_ID` — message your new bot once, then check the
     `message.chat.id` field in a `getUpdates` response (or ask
     [@userinfobot](https://t.me/userinfobot) for your own chat id). This
     matters: without it, anyone who finds your bot's username could queue
     words into your deck.
   - `ANKI_COLLECTION_PATH` — the absolute path to your `collection.anki2`,
     typically
     `/Users/<you>/Library/Application Support/Anki2/User 1/collection.anki2`.
   - `PROJECT_DIR` — the absolute path to this checkout on the machine that
     will run it.
   - `WAKE_TIMES` — comma-separated `HH:MM` (24h, local time) run times,
     e.g. `08:00,20:00`.

   Everything else has a sensible default in `.env.example`.

## One-time manual Anki setup

In Anki.app, open **Preferences → Sync** and enable **"Automatically sync
on profile open and close."** The sync-trigger step (below) relies entirely
on this — without it, opening and quitting Anki.app does nothing to sync
your new cards to AnkiWeb/mobile.

## Running it manually

- `vocab-drain` — fetch queued Telegram messages into
  `state/pending_words.jsonl` and advance the Telegram offset. Doesn't
  touch Anki at all. Use this alone to see what's queued.
- `vocab-process` — look up definitions for every pending word and write
  new notes into your Anki collection. Quits Anki.app first if it's
  running. Doesn't drain Telegram or trigger a sync.
- `vocab-batch` — runs `vocab-drain` → `vocab-process` → a sync trigger, in
  that order, and appends a summary line to `logs/run_log.txt`. This is
  what the scheduler calls; run it manually to test the whole pipeline
  end-to-end before scheduling anything.

Each phase is isolated from the others' failures — e.g. if Telegram is
unreachable, `vocab-batch` still processes anything already queued and
still triggers a sync.

## Scheduling

```bash
vocab-setup-schedule
```

This calls `sudo pmset`, so it will prompt for your account password.

Reads `WAKE_TIMES` from `.env` and, in one step:

- Registers a `pmset repeat wake` entry for the **earliest** configured
  time, so the Mac wakes from sleep (lid closed, no external display
  needed) at that time. macOS only supports one recurring wake event (see
  `man pmset`) — passing more than one `WAKE_TIMES` entry to `pmset` would
  just have each call silently overwrite the last, so only the earliest is
  registered there.
- Writes a LaunchAgent plist to
  `~/Library/LaunchAgents/com.user.vocabpipeline.plist` with one
  `StartCalendarInterval` entry **per** configured wake time, pointing at
  the installed `vocab-batch` executable, and loads it with `launchctl`.
  The plist sets `WorkingDirectory` to `PROJECT_DIR` so the scheduled runs
  reliably find `.env` (launchd otherwise gives the process no working
  directory to search from).

Net effect: every `WAKE_TIMES` entry triggers a run if the Mac is already
awake at that moment, but only the earliest one will actually **wake it
from sleep**. For a machine that's sleeping most of the day, put your
most important run time first, or accept that later times only catch runs
opportunistically. `vocab-setup-schedule` prints which time was registered
with `pmset` after it runs.

A LaunchAgent (not a LaunchDaemon) is required because triggering Anki's
sync needs a logged-in GUI session to launch Anki.app in. That means:
staying logged in (the screen can lock, but the session must stay live),
and never fully powering off the machine — sleep only. Telegram's own
server-side retention is a hard 24-hour ceiling (see below), so schedule at
least two wake times for margin, keeping in mind only the first reliably
wakes the machine.

To change the schedule later, edit `WAKE_TIMES` in `.env` and re-run
`vocab-setup-schedule` — never hand-edit the plist or `pmset` entries
directly.

## Architecture

```
Telegram (server-side queue, 24h retention)
        │  getUpdates (long poll, one-shot)
        ▼
[1] vocab-drain   ──appends──▶  state/pending_words.jsonl (durable local store)
        │  advances the Telegram offset only after the local write succeeds
        ▼
[2] vocab-process
        │  for each pending word: dictionary lookup (fallback chain),
        │  quit Anki.app if open, write a note via the `anki` pylib,
        │  mark the word done/failed in pending_words.jsonl
        ▼
[3] sync trigger
        │  open Anki.app (sync-on-open) → wait → quit again
        ▼
vocab-batch chains [1]→[2]→[3] and logs a summary; a LaunchAgent + pmset
wake schedule runs it unattended, a few times a day.
```

See [`PLAN.md`](PLAN.md) for the full design rationale, including why each
piece was built this way.

## Logs & troubleshooting

- `logs/run_log.txt` — one line per `vocab-batch` run: timestamp, how many
  words were drained/processed/succeeded/failed, whether the sync trigger
  ran, and any per-phase errors.
- `logs/launchd.out.log` / `logs/launchd.err.log` — stdout/stderr from the
  LaunchAgent-triggered runs. Check these first if `run_log.txt` isn't
  gaining new lines on schedule.
- `launchctl list | grep vocabpipeline` — confirms the LaunchAgent is
  loaded. An entry with a non-zero last exit code means the last run
  crashed before writing its log line.
- A `"failed"` entry in `state/pending_words.jsonl` means that word's
  lookup or Anki write raised an error — check its `error` field. To
  retry it, edit that line's `"status"` back to `"pending"` (and clear
  `"error"`) and run `vocab-process` again.
- Nothing in `pending_words.jsonl` for words you know you sent, more than
  24 hours ago? They're gone — see the Telegram retention limit below.

## Known limitations

- **Telegram's 24-hour retention cap is absolute.** If the Mac is fully
  shut down (not sleeping) for over a day, words sent in that window are
  unrecoverable — this is Telegram's server-side limit, not a bug here.
  Scheduling multiple wake times a day gives margin but doesn't remove the
  ceiling.
- **Only one wake time actually wakes the Mac from sleep.** `pmset repeat`
  supports a single recurring wake event, so `vocab-setup-schedule`
  registers only the earliest `WAKE_TIMES` entry with it. Other entries
  still trigger `vocab-batch` via the LaunchAgent, but only when the Mac
  happens to already be awake at that time.
- **The `anki` pip package must be kept in sync with Anki.app.** Anki.app
  auto-updates; re-check the pin in `pyproject.toml` after any major
  update, or collection writes may fail (safer than silent corruption, but
  still worth watching for in `run_log.txt`).
- **The sync-trigger wait is a heuristic, not a completion signal.**
  `ANKI_SYNC_WAIT_SECONDS` (default 45s) is generous for a small personal
  deck, but glance at the logs occasionally rather than assuming sync
  always finishes in time.
- **No real-time confirmation.** There's no persistent listener, so you
  won't get an instant "added ✓" reply in Telegram — confirmation only
  happens at the next scheduled `vocab-batch` run (or by checking
  `pending_words.jsonl` / `run_log.txt` yourself).
