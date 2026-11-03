"""Command line interface: `ai-usage [status|limits|tokens|tray|autostart]`."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path

from ai_usage import __version__, autostart, config, fmt
from ai_usage.aggregate import GROUPINGS, PERIODS, group_by, in_period, totals
from ai_usage.models import LimitsSnapshot, TokenTotals
from ai_usage.providers import get_provider
from ai_usage.providers.base import Provider

TOKEN_HEADERS = ["Input", "Output", "Cache wr", "Cache rd", "Total", "Req"]


def _token_cells(t: TokenTotals) -> list[str]:
    return [
        fmt.human_tokens(t.input),
        fmt.human_tokens(t.output),
        fmt.human_tokens(t.cache_write),
        fmt.human_tokens(t.cache_read),
        fmt.human_tokens(t.total),
        str(t.requests),
    ]


def _limits_json(s: LimitsSnapshot) -> dict:
    return {
        "provider": s.provider,
        "plan": s.plan,
        "fetched_at": s.fetched_at.isoformat() if s.fetched_at else None,
        "stale": s.stale,
        "error": s.error,
        "windows": [
            {
                "key": w.key,
                "group": w.group,
                "label": w.label,
                "percent": w.percent,
                "severity": w.severity,
                "resets_at": w.resets_at.isoformat() if w.resets_at else None,
            }
            for w in s.windows
        ],
    }


def _render_limits(provider: Provider, s: LimitsSnapshot) -> str:
    plan = f" · {s.plan} plan" if s.plan else ""
    lines = [f"{provider.title}{plan}"]
    if s.windows:
        width = max(len(w.label) for w in s.windows)
        for w in s.windows:
            lines.append(
                f"  {w.label.ljust(width)}  {fmt.percent_text(w.percent):>4}  "
                f"{fmt.bar(w.percent)}  resets {fmt.reset_text(w.resets_at)}"
            )
    else:
        lines.append("  No limit data available")
    if s.error:
        cached = f" Showing data from {fmt.age_text(s.fetched_at)}." if s.windows else ""
        lines.append(f"  ! {s.error}.{cached}")
    return "\n".join(lines)


def cmd_status(provider: Provider, args) -> int:
    snapshot = provider.fetch_limits()
    records = provider.usage_records()
    rows = {p: totals(in_period(records, p)) for p in ("today", "7d")}
    if args.json:
        print(json.dumps({"limits": _limits_json(snapshot), "tokens": {p: t.as_dict() for p, t in rows.items()}}, indent=2))
        return 0
    print(_render_limits(provider, snapshot))
    print()
    print(f"Tokens ({provider.usage_scope})")
    table_rows = [[PERIODS[p], *_token_cells(t)] for p, t in rows.items()]
    print(fmt.table(["", *TOKEN_HEADERS], table_rows))
    return 0


def cmd_limits(provider: Provider, args) -> int:
    snapshot = provider.fetch_limits()
    if args.json:
        print(json.dumps(_limits_json(snapshot), indent=2))
    else:
        print(_render_limits(provider, snapshot))
    return 0 if snapshot.windows else 1


def cmd_tokens(provider: Provider, args) -> int:
    records = in_period(provider.usage_records(), args.period)
    groups = group_by(records, args.by)
    total = totals(records)  # hidden projects still count here (they're only off the list)
    hidden = config.hidden_projects() if args.by == "project" else set()
    if not args.show_hidden:
        groups = [(name, t) for name, t in groups if name not in hidden]
    if args.json:
        group_rows = []
        for name, t in groups:
            row = {"name": name, **t.as_dict()}
            if name in hidden:
                row["hidden"] = True
            group_rows.append(row)
        print(
            json.dumps(
                {"period": args.period, "by": args.by, "groups": group_rows, "total": total.as_dict()},
                indent=2,
            )
        )
        return 0
    print(f"{provider.title} tokens · {PERIODS[args.period]} · by {args.by} ({provider.usage_scope})")
    if not groups:
        print("No usage in this period.")
        return 0
    rows = [[f"{name} (hidden)" if name in hidden else name, *_token_cells(t)] for name, t in groups]
    rows.append(["Total", *_token_cells(total)])
    print(fmt.table([args.by.capitalize(), *TOKEN_HEADERS], rows))
    return 0


def cmd_tray(provider: Provider, args) -> int:
    try:
        from ai_usage.tray.app import run
    except (ImportError, ValueError) as e:
        print(
            f"Tray needs GTK 3 and AyatanaAppIndicator3 ({e}).\n"
            "Install: sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1",
            file=sys.stderr,
        )
        return 1
    return run(provider, limits_interval=args.interval)


def _normalize_project(query: str) -> str:
    query = query.rstrip("/") or query
    query = str(Path(query).expanduser()) if query.startswith("~") else query
    home = str(Path.home())
    if query == home or query.startswith(home + "/"):
        return "~" + query[len(home):]
    return query


def resolve_project(query: str, candidates: Iterable[str], allow_new_path: bool = False) -> str:
    """Exact name/path, else a unique case-insensitive substring (or exact basename)."""
    names = sorted(set(candidates))
    normalized = _normalize_project(query)
    if normalized in names:
        return normalized
    needle = query.lower()
    matches = [n for n in names if needle in n.lower()]
    if len(matches) > 1:
        by_basename = [n for n in matches if n.rstrip("/").rsplit("/", 1)[-1].lower() == needle]
        if len(by_basename) == 1:
            return by_basename[0]
        raise ValueError(f"'{query}' matches several projects:\n  " + "\n  ".join(matches))
    if matches:
        return matches[0]
    if allow_new_path and normalized.startswith(("/", "~")):
        return normalized  # not seen in the logs yet: hide it in advance
    raise ValueError(f"No project matches '{query}'.")


def cmd_hide(provider: Provider, args) -> int:
    known = {r.project for r in provider.usage_records()}
    try:
        projects = [resolve_project(q, known, allow_new_path=True) for q in args.projects]
        for project in projects:
            config.set_hidden(project, True)
    except (ValueError, OSError) as e:
        print(f"ai-usage: {e}", file=sys.stderr)
        return 1
    for project in projects:
        print(f"Hidden: {project}")
    return 0


def cmd_unhide(provider: Provider, args) -> int:
    try:
        if args.all:
            print(f"Unhid {config.unhide_all()} project(s).")
            return 0
        if not args.projects:
            print("ai-usage: name projects to unhide, or use --all", file=sys.stderr)
            return 2
        projects = [resolve_project(q, config.hidden_projects()) for q in args.projects]
        for project in projects:
            config.set_hidden(project, False)
    except (ValueError, OSError) as e:
        print(f"ai-usage: {e}", file=sys.stderr)
        return 1
    for project in projects:
        print(f"Visible again: {project}")
    return 0


def cmd_autostart(provider: Provider, args) -> int:
    if args.action == "enable":
        print(f"Enabled: {autostart.enable()}")
    elif args.action == "disable":
        print("Disabled." if autostart.disable() else "Was not enabled.")
    else:
        path = autostart.autostart_file()
        print(f"Enabled ({path})" if autostart.is_enabled() else "Disabled")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-usage", description="AI subscription limits and token usage.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--provider", default="claude", choices=["claude"], help=argparse.SUPPRESS)
    # The metavar lists only the public commands; `hide` / `unhide` are added without
    # help text, so they stay out of --help (the hidden-projects feature is a quiet one).
    sub = parser.add_subparsers(dest="command", metavar="{status,limits,tokens,tray,autostart}")

    p = sub.add_parser("status", help="limits + today/7-day tokens (default)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("limits", help="plan limit windows")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_limits)

    p = sub.add_parser("tokens", help="token usage table")
    p.add_argument("--period", choices=list(PERIODS), default="today")
    p.add_argument("--by", choices=GROUPINGS, default="model")
    p.add_argument("--json", action="store_true")
    p.add_argument("--show-hidden", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_tokens)

    p = sub.add_parser("tray", help="run the top bar indicator")
    p.add_argument("--interval", type=int, default=180, help="limits refresh interval, seconds (min 60)")
    p.set_defaults(func=cmd_tray)

    p = sub.add_parser("autostart", help="start the tray on login")
    p.add_argument("action", choices=["enable", "disable", "status"], nargs="?", default="status")
    p.set_defaults(func=cmd_autostart)

    p = sub.add_parser("hide", description="Hide projects from the per-project lists.")
    p.add_argument("projects", nargs="+", metavar="PROJECT", help="path, name or unique part of it")
    p.set_defaults(func=cmd_hide)

    p = sub.add_parser("unhide", description="Show hidden projects again.")
    p.add_argument("projects", nargs="*", metavar="PROJECT")
    p.add_argument("--all", action="store_true", help="unhide every project")
    p.set_defaults(func=cmd_unhide)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args.json, args.func = False, cmd_status
    try:
        return args.func(get_provider(args.provider), args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
