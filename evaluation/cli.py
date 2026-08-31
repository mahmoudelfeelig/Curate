from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .suite import run_suite


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "evaluations" / "feed-passport-evaluation.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Feed Passport's deterministic, offline acceptance evaluation."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="JSON report path (default: artifacts/evaluations/feed-passport-evaluation.json).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Optional parent for temporary SQLite state. State is removed after the run.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_suite(work_dir=args.work_dir)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"{report['summary']['status']}: {report['summary']['passed']}/"
        f"{report['summary']['total']} scenarios; report={output}"
    )
    return 0 if report["summary"]["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
