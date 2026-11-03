# ai-usage

A small Linux tray widget + CLI that shows your AI subscription limits and token usage.
It currently supports **Claude** (Claude Code / claude.ai plans).

```
$ ai-usage
Claude · max plan
  Session (5h)          42%  ████████░░░░░░░░░░░░  resets today 19:00 (in 1h 45m)
  Weekly (all models)   17%  ███░░░░░░░░░░░░░░░░░  resets Thu 20:00 (in 2d 3h)

Tokens (Claude Code, this machine)
             Input  Output  Cache wr  Cache rd  Total  Req
───────────  ─────  ──────  ────────  ────────  ─────  ───
Today          500    150k      1.2M     40.5M  41.9M  210
Last 7 days   3.1k    900k      5.4M      180M   186M  950
```

The top bar shows a ring gauge (session usage) and a label such as `42% · 17%`
(session · weekly). The menu lists every limit window, today's and the last 7 days'
token totals, and opens a statistics window with breakdowns by model, project and day.

## Requirements

- Linux with a StatusNotifier/AppIndicator tray. Tested on Ubuntu 24.04 + GNOME 46
  (the `ubuntu-appindicators` extension is on by default).
- System Python 3.10+ with `python3-gi`, `gir1.2-gtk-3.0`, `gir1.2-ayatanaappindicator3-0.1`
  (preinstalled on Ubuntu desktop). The CLI needs only Python.
- Claude Code logged in with a Claude subscription (`claude` → `/login`).

## Install

```bash
./install.sh                 # ~/.local/bin/ai-usage + app launcher (runs from this checkout)
ai-usage autostart enable    # optional: start the tray on login
ai-usage tray &              # start it now
./install.sh --uninstall     # remove
```

Without installing: `PYTHONPATH=src python3 -m ai_usage`.

## Commands

| Command | What it does |
|---|---|
| `ai-usage` / `ai-usage status [--json]` | limits + Today / Last 7 days tokens |
| `ai-usage limits [--json]` | limit windows only |
| `ai-usage tokens [--period today\|7d\|30d\|all] [--by model\|project\|day] [--json]` | token table |
| `ai-usage tray [--interval 180]` | top bar indicator (limits refresh in seconds, min 60) |
| `ai-usage autostart [enable\|disable\|status]` | start on login |

## Hiding projects

Right-click a project in the statistics window's **By project** tab and choose
**Hide project**. It disappears from the per-project lists (that tab and
`ai-usage tokens --by project`). Totals and the model/day breakdowns still count it.

Hidden projects are deliberately not advertised in the UI:

- **Ctrl+Shift+H** in the statistics window shows hidden projects, marked *(hidden)*.
  Right-click one to unhide it, or press **Unhide all**. The mode switches off when you
  close the window.
- CLI: `ai-usage hide <project>` (a path, a name or a unique part of it),
  `ai-usage unhide <project>` / `ai-usage unhide --all`, and
  `ai-usage tokens --by project --show-hidden`. These aren't listed in `--help`.

The list is stored in `~/.config/ai-usage/config.json`. This keeps projects off the
screen (e.g. while screen sharing); it's not access control.

## Where the numbers come from

- **Limits**: `GET https://api.anthropic.com/api/oauth/usage`, the endpoint behind
  Claude Code's `/usage`, called with the OAuth token from `~/.claude/.credentials.json`.
  It's **undocumented** and may change; the parser ignores unknown fields.
  The token is only read: ai-usage never refreshes or writes it. When it has expired,
  open `claude` once and it refreshes the token itself.
  The last good response is cached in `~/.cache/ai-usage/` and shown as stale on errors.
- **Tokens**: Claude Code transcripts in `~/.claude/projects/**/*.jsonl`
  (or `$CLAUDE_CONFIG_DIR`). Streaming writes each response several times, so the
  scanner deduplicates by message and request id and keeps the final copy.
  Projects are the directories `claude` was started in.

Limit percentages include all your Claude usage (web, desktop, other machines). Token
totals cover only Claude Code on this machine, and only transcripts Claude Code hasn't
cleaned up yet (default retention is 30 days). "Total" includes cache reads, which
usually dominate.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

Docs: [requirements](docs/REQUIREMENTS.md), [plan](docs/PLAN.md).
To add a provider, implement `providers/base.py:Provider` and register it in
`providers/__init__.py`.
