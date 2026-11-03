# ai-usage — Work plan

Requirements: [REQUIREMENTS.md](REQUIREMENTS.md).

## Stack decision

| Concern | Choice | Why |
|---|---|---|
| Language | System Python 3.12 | `python3-gi` is preinstalled; no venv or pip deps needed |
| Tray | AyatanaAppIndicator3 (GTK 3) | Ubuntu ships and enables `ubuntu-appindicators`; supports a text label |
| Window | GTK 3 (`Gtk.TreeView`) | AppIndicator is GTK 3, and GTK 3 and 4 can't share one process |
| HTTP / JSON | `urllib`, `json` | one GET request; no need for `requests` |
| Tests | `unittest` | zero deps; `python3 -m unittest` |

Rejected: GNOME Shell extension (tied to GNOME versions, harder to debug),
Tauri/Electron (heavy for a tray widget), CLI-only (not visible at a glance).

## Layout

```
src/ai_usage/
  models.py            LimitWindow, LimitsSnapshot, UsageRecord, TokenTotals
  aggregate.py         periods, totals, group by model/project/day
  fmt.py               human numbers, reset times, bars, text tables
  cache.py             last-known limits on disk
  backoff.py           polling interval with exponential backoff
  autostart.py         ~/.config/autostart desktop entry
  cli.py               argparse entry point (status|limits|tokens|tray|autostart)
  providers/base.py    Provider interface
  providers/claude/    paths, credentials, limits (API), logs (JSONL scanner), provider
  tray/                app.py (indicator + menu + scheduling), icons.py, window.py
tests/                 unittest suites + fixtures
install.sh             wrapper + launcher install/uninstall
```

## Phases

- [x] **0. Research & stack**: data sources verified live; stack chosen (see above).
- [x] **1. Requirements**: `docs/REQUIREMENTS.md`.
- [x] **2. Plan**: this file.
- [x] **3. Skeleton**: `pyproject.toml`, package layout, `.gitignore`, test runner.
- [x] **4. Claude core**: paths and credentials; limits client + defensive parser + cache;
      JSONL scanner (prefilter, dedup by max output, incremental offsets); aggregation.
      Tests with fixtures.
- [x] **5. CLI**: `status`, `limits`, `tokens` with text and `--json` output.
- [x] **6. Tray**: indicator, gauge icons, menu, background workers, backoff, single instance.
- [x] **7. Statistics window**: limit bars, period selector, model/project/day tabs.
- [x] **8. Autostart + install.sh + README**.
- [x] **9. Verification**: unit tests green; CLI on real data; token totals cross-checked
      against an independent `jq` count; tray registered on D-Bus and screenshotted.

## Verification results (2026-11-03)

- 41 unit tests pass (`PYTHONPATH=src python3 -m unittest discover -s tests -t .`).
- All-time totals match an independent `jq` dedup on the same data (identical response counts and token sums).
- Tray registers as `/org/ayatana/NotificationItem/ai_usage`; label, icon and menu checked
  over D-Bus and on screen; the statistics window opens from the menu.
- Found and fixed: quitting during the first log scan took 3–10+ s, because the executor
  threads were non-daemon. Background jobs now run in daemon threads, and quitting takes ~0.1 s.
- `install.sh`, autostart enable/disable and `--uninstall` tested against a throwaway HOME.
- Found and fixed: the top bar label didn't show when the tray started with cached limits.
  The `ubuntu-appindicators` extension only picks up a label from the `XAyatanaNewLabel`
  signal (it doesn't read the property when an indicator appears), and libayatana only
  emits that signal when the text changes. The tray now re-sends the label 1/3/6 s after
  connecting, and on every refresh it alternates an invisible zero-width space suffix.

## Later (not v0.1)

Threshold notifications; $ estimate; Codex CLI / Gemini CLI providers; a persistent
token history so totals survive transcript cleanup; .deb packaging.
