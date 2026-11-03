# ai-usage — Requirements (v0.1: Claude)

## Goal

A small Linux desktop utility that shows, at a glance from the GNOME top bar,
how much of the Claude subscription limits is used and how many tokens were spent,
plus a CLI for the same data. Built so other AI providers can be added later.

## Target environment

- Ubuntu 24.04, GNOME 46 (X11 and Wayland) with the `ubuntu-appindicators` extension.
- System Python 3.12 + PyGObject (`python3-gi`), GTK 3, `gir1.2-ayatanaappindicator3-0.1`.
- No third-party Python packages at runtime (stdlib + system GI bindings only).

## Data sources

### DS-1. Plan limits — `GET https://api.anthropic.com/api/oauth/usage`

- Auth: `Authorization: Bearer <claudeAiOauth.accessToken>` read from
  `<claude-config>/.credentials.json`, header `anthropic-beta: oauth-2025-04-20`.
- The endpoint is **undocumented** (it backs Claude Code's `/usage`) and may change.
  Parse defensively: ignore unknown or `null` fields; never crash on a schema change.
- Use the `limits[]` array when it is present and non-empty
  (`kind`, `group`, `percent`, `resets_at`). Otherwise fall back to the flat keys
  `five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet` (`utilization`, `resets_at`).
- Observed response shape (2026-11): `five_hour`, `seven_day` and `limits[]` with
  `session` and `weekly_all` entries; many codename keys are `null`.

### DS-2. Local Claude Code transcripts

- `<claude-config>/projects/**/*.jsonl`, including `<session>/subagents/*.jsonl`.
- `<claude-config>` = each entry of `$CLAUDE_CONFIG_DIR` (comma-separated), otherwise
  `~/.config/claude` and `~/.claude` (whichever exist).
- Usage record: line with `type == "assistant"` and `message.usage`:
  `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`,
  plus `message.model`, `timestamp` (ISO, UTC), `cwd`, `sessionId`.
- **Deduplication:** one API response is written 1–13 times while streaming.
  Key = (`message.id`, `requestId`). Later copies carry the final `output_tokens`,
  so keep the copy with the **largest** `output_tokens`.
- Skip model `<synthetic>`; skip malformed lines.
- Volume: transcripts grow to hundreds of MB (mostly request blobs without usage);
  a full scan of ~500 MB takes ~2 s.
  → prefilter lines by bytes before `json.loads`, then read files incrementally
  (remember a byte offset per file, consume only complete lines).

### Known limitation

Local logs cover only Claude Code on this machine. The limit percentages also include
claude.ai web/desktop/mobile and other machines, so the token totals and the limit %
won't match exactly. The UI says this ("Claude Code, this machine").
Claude Code deletes old transcripts (`cleanupPeriodDays`, default 30), so "All time"
only covers the transcripts that still exist.

## Functional requirements

- **FR-1 `ai-usage` / `ai-usage status`**: plan type; each limit window with %, a bar,
  and its reset time (local time + relative); token summary for Today and the last 7 days.
- **FR-2 `ai-usage limits [--json]`**: limit windows only.
- **FR-3 `ai-usage tokens [--period today|7d|30d|all] [--by model|project|day] [--json]`**:
  table with Input, Output, Cache write, Cache read, Total, Requests, plus a total row.
- **FR-4 `ai-usage tray`**: an AppIndicator in the top bar:
  - label `42% · 17%` (session · weekly); a ring-gauge icon for the session %,
    coloured by the highest window: normal < 80%, warning 80–95%, critical ≥ 95%;
  - menu: plan header; one row per limit window (% + reset); Today and 7-day token totals;
    `Statistics…`; `Refresh now`; `Start on login` toggle; status line
    (`Updated 17:20` / error text); `Quit`;
  - refresh limits every 3 min (`--interval`, min 60 s), rescan logs every 60 s;
    I/O runs off the GTK main thread, so the UI never blocks;
  - single instance (a second `ai-usage tray` exits with a message).
- **FR-5 Statistics window**: limit progress bars; period selector
  (Today / 7 days / 30 days / All time); tabs By model / By project / By day;
  sortable columns; a totals line.
- **FR-6 Autostart**: `ai-usage autostart enable|disable|status` and the menu toggle;
  manages `~/.config/autostart/ai-usage.desktop`.
- **FR-7 Install**: `./install.sh` → `~/.local/bin/ai-usage` wrapper (runs from the repo)
  + launcher `~/.local/share/applications/ai-usage.desktop`; `./install.sh --uninstall`.
- **FR-8 Cache**: the last successful limits response is stored in
  `$XDG_CACHE_HOME/ai-usage/claude-limits.json` and shown, marked stale with its age,
  when a fresh fetch fails.

- **FR-9 Hidden projects**: right-click a project in the "By project" tab → Hide project;
  CLI `hide <project>` (path, name or unique substring). Hidden projects are left out of
  the per-project lists only; totals and the model/day breakdowns still include them.
  Reveal is deliberately unadvertised: **Ctrl+Shift+H** in the statistics window toggles a
  mode that shows hidden projects (marked, unhide via right-click or "Unhide all") and
  switches off when the window closes; CLI `unhide <project>|--all` and
  `tokens --show-hidden`, both missing from `--help`. Stored in
  `$XDG_CONFIG_HOME/ai-usage/config.json`.

- **FR-10 Timeline**: a Timeline tab (replaces "By day") with a column chart. One shared
  period selector (Today / 7 / 30 / 90 days / 12 months / All time, remembered) scopes
  the tables and the timeline alike; data outside it is left out so sums agree. Bar size
  day / week / month (remembered), Today by hour; a size needing more than 120 bars is
  disabled for that period and the next coarser one is shown, without overwriting the
  saved choice. Metric switch (Output by default, Total, Requests, Input + cache write),
  project filter (hidden projects listed only in reveal mode), average reference line,
  comparison with the equally long span before the period (shown only when the history
  covers it), hover tooltips, a table of every bar. Bars covering only part of their
  span (running period, cut by the period start, history starting inside) are lighter
  and left out of the average; days before the history aren't drawn.
- **FR-11 Persistent history**: every log scan folds hourly totals per (UTC hour, model,
  project key) into `$XDG_DATA_HOME/ai-usage/claude-history.json` with a per-counter
  max merge, so deleted transcripts never lower the history. An unreadable file is
  moved aside, never overwritten. Numbers only.

## Non-functional requirements

- **NFR-1 Read-only towards Claude**: never write the credentials file, never refresh the
  OAuth token (refresh tokens rotate; refreshing could log Claude Code out).
  Expired token → `Token expired — run claude to refresh it`, plus cached data.
- **NFR-2 Secrets**: the access token is never printed, logged, cached or put in `repr()`.
- **NFR-3 Polite polling**: 10 s timeout; on HTTP 429 or network errors, back off
  exponentially (×2, max 30 min), honour `Retry-After`; reset after a success.
- **NFR-4 Performance**: first log scan in the background < 3 s; incremental rescan
  < 200 ms; aggregation for the UI < 50 ms.
- **NFR-5 Resilience**: missing `~/.claude`, missing credentials, malformed lines,
  partially written last lines, and unknown API fields are all handled without crashing.
- **NFR-6 Extensibility**: a provider interface (`fetch_limits()`, `usage_records()`);
  Claude is the first implementation; UI/CLI code doesn't depend on Claude specifics.
- **NFR-7 Tests**: core logic (parsing, dedup, incremental reads, aggregation, formatting,
  backoff, cache) covered by stdlib `unittest`; no network in tests.

## Out of scope for v0.1

Desktop notifications, $ cost estimates, other providers (Codex CLI, Gemini CLI, …),
Anthropic Admin/Usage API org reports, packaging (.deb/Flatpak), non-GNOME desktops (may work, untested).
