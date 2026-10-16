"""Explicit CLI for building the indicator lake.

The turbo/CLI screening path reads precomputed per-symbol indicators from
``data/indicators/precomputed``. Those artifacts are produced by
``LibrarianIntelligenceMixin.build_indicator_lake`` — and by nothing else.

That is deliberate. Before this module existed there was no way to build the
lake at all: the only producer, ``update_indicator_history``, sat behind a
``skip_maintenance`` early return that *every* call site triggered, so the
directory stayed empty while the sync reported success. The fix is not to
trigger a silent multi-thousand-symbol write from a read path — it is to make
the build an explicit, bounded, resumable operator action.

Usage::

    python -m myra_app.lake_build --status          # probe only, no writes
    python -m myra_app.lake_build --limit 25        # stage a small batch
    python -m myra_app.lake_build --symbols AAA,BBB
    python -m myra_app.lake_build                   # everything not fresh
    python -m myra_app.lake_build --force           # ignore the freshness gate

Re-running is cheap and safe: symbols whose artifact is already at least as new
as the newest stored session are skipped, each file is written atomically, and a
symbol with unusable history is left absent rather than written with zeroed
indicators.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading


def _parse_symbols(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [s.strip().upper() for s in value.split(",") if s.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m myra_app.lake_build",
        description="Build or refresh the indicator lake the turbo scan reads.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report the lake's current state and exit. Never writes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Bound this invocation to at most N symbols.",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbols to build instead of the active universe.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even artifacts that are already as new as the latest session.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Suppress the per-symbol progress line.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    from myra_app.indicator_lake import status as lake_status

    if args.status:
        print(json.dumps(lake_status(reference_date=None), indent=2, default=str))
        return 0

    limit = args.limit
    if limit is not None and limit <= 0:
        print("[!] --limit must be a positive integer.", file=sys.stderr)
        return 2

    from myra_app.librarian import Librarian

    cancel_event = threading.Event()
    lib = Librarian()
    try:
        summary = lib.build_indicator_lake(
            symbols=_parse_symbols(args.symbols),
            force=args.force,
            max_symbols=limit,
            progress=not args.no_progress,
            cancel_event=cancel_event,
        )
    except KeyboardInterrupt:
        cancel_event.set()
        print(
            "\n[!] Interrupted. Artifacts already written are complete and "
            "valid; re-run to resume from where it stopped.",
            file=sys.stderr,
        )
        return 130

    print(json.dumps(summary, indent=2, default=str))
    if summary.get("failed"):
        print(
            f"[!] {summary['failed']} symbol(s) failed. See 'failures' above.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - thin entry point
    raise SystemExit(main())
