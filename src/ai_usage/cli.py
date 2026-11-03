"""Command line interface: `ai-usage [status|limits|tokens|tray|autostart]`."""

from __future__ import annotations

import argparse
import json
import sys

from ai_usage import __version__, autostart, fmt
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
    total = totals(records)
    if args.json:
        print(
            json.dumps(
                {
                    "period": args.period,
                    "by": args.by,
                    "groups": [{"name": name, **t.as_dict()} for name, t in groups],
                    "total": total.as_dict(),
                },
                indent=2,
            )
        )
        return 0
    print(f"{provider.title} tokens · {PERIODS[args.period]} · by {args.by} ({provider.usage_scope})")
    if not groups:
        print("No usage in this period.")
        return 0
    rows = [[name, *_token_cells(t)] for name, t in groups]
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
    sub = parser.add_subparsers(dest="command")

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
    p.set_defaults(func=cmd_tokens)

    p = sub.add_parser("tray", help="run the top bar indicator")
    p.add_argument("--interval", type=int, default=180, help="limits refresh interval, seconds (min 60)")
    p.set_defaults(func=cmd_tray)

    p = sub.add_parser("autostart", help="start the tray on login")
    p.add_argument("action", choices=["enable", "disable", "status"], nargs="?", default="status")
    p.set_defaults(func=cmd_autostart)
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
