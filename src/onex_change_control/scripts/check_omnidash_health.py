# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Post-merge behavioral probe for omnidash_analytics table row counts.

Connects to the omnidash_analytics database and compares current table row
counts against a persisted baseline.  Reports regressions (table went to 0)
and warnings (>50 % drop) so callers can surface them via Discord/Slack.

Usage:
    uv run check-omnidash-health
    uv run check-omnidash-health --save-baseline
    uv run check-omnidash-health --json

Exit codes:
    0: No regressions detected
    1: At least one REGRESSION detected (table dropped to 0)
    2: Connection or runtime error
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _load_password() -> str:
    """Resolve POSTGRES_PASSWORD from env or by sourcing ~/.omnibase/.env."""
    pw = os.environ.get("POSTGRES_PASSWORD")
    if pw:
        return pw

    env_file = Path.home() / ".omnibase" / ".env"
    if not env_file.exists():
        print(
            f"ERROR: POSTGRES_PASSWORD not set and {env_file} not found",
            file=sys.stderr,
        )
        sys.exit(2)

    # Source the env file in a subshell and extract the variable
    try:
        result = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "bash",
                "-c",
                f"source {env_file} && echo $POSTGRES_PASSWORD",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        pw = result.stdout.strip()
    except subprocess.CalledProcessError:
        pw = ""

    if not pw:
        print(
            f"ERROR: Could not resolve POSTGRES_PASSWORD from env or {env_file}",
            file=sys.stderr,
        )
        sys.exit(2)

    return pw


def _fetch_row_counts(password: str) -> dict[str, int]:
    """Query pg_stat_user_tables for live tuple counts."""
    try:
        import psycopg2
    except ImportError:
        print(
            "ERROR: psycopg2 is not installed. "
            'Install with: uv pip install "onex-change-control[postgres]"',
            file=sys.stderr,
        )
        sys.exit(2)

    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5436")
    dbname = os.environ.get("OMNIDASH_DB_NAME", "omnidash_analytics")
    user = os.environ.get("POSTGRES_USER", "postgres")

    try:
        conn = psycopg2.connect(
            host=host,
            port=int(port),
            dbname=dbname,
            user=user,
            password=password,
            connect_timeout=5,
        )
    except psycopg2.OperationalError as exc:
        print(
            f"ERROR: Cannot connect to {dbname}@{host}:{port} — {exc}",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname"
            )
            rows: dict[str, int] = {row[0]: int(row[1]) for row in cur.fetchall()}
    finally:
        conn.close()

    return rows


# ---------------------------------------------------------------------------
# Baseline helpers
# ---------------------------------------------------------------------------

DEFAULT_BASELINE_PATH = Path.home() / ".omnibase" / "omnidash_baseline.json"


def _load_baseline(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: int(v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError):
        pass
    return {}


def _save_baseline(path: Path, counts: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(counts, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


class Finding:
    def __init__(
        self,
        table: str,
        level: str,
        baseline: int,
        current: int,
    ) -> None:
        self.table = table
        self.level = level  # "REGRESSION" | "WARNING"
        self.baseline = baseline
        self.current = current

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "level": self.level,
            "baseline": self.baseline,
            "current": self.current,
        }


def compare(
    baseline: dict[str, int],
    current: dict[str, int],
) -> list[Finding]:
    """Compare current row counts against baseline."""
    findings: list[Finding] = []

    for table, prev in sorted(baseline.items()):
        now = current.get(table, 0)

        if prev > 0 and now == 0:
            findings.append(
                Finding(table, "REGRESSION", prev, now),
            )
        elif prev > 0 and now < prev * 0.5:
            findings.append(
                Finding(table, "WARNING", prev, now),
            )

    return findings


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def _format_text_report(
    findings: list[Finding],
    current: dict[str, int],
) -> str:
    lines: list[str] = []
    lines.append("=== omnidash_analytics health check ===")
    lines.append(f"Tables scanned: {len(current)}")

    regressions = [f for f in findings if f.level == "REGRESSION"]
    warnings = [f for f in findings if f.level == "WARNING"]

    if not findings:
        lines.append("Status: HEALTHY — no regressions or warnings")
        return "\n".join(lines)

    if regressions:
        lines.append("")
        lines.append(f"REGRESSIONS ({len(regressions)}):")
        for f in regressions:
            lines.append(f"  [REGRESSION] {f.table}: {f.baseline} -> {f.current} rows")

    if warnings:
        lines.append("")
        lines.append(f"WARNINGS ({len(warnings)}):")
        for f in warnings:
            pct = round((1 - f.current / f.baseline) * 100) if f.baseline > 0 else 0
            lines.append(
                f"  [WARNING] {f.table}: {f.baseline} -> {f.current} rows (-{pct}%)"
            )

    lines.append("")
    if regressions:
        lines.append("Result: FAIL — regressions detected")
    else:
        lines.append("Result: PASS (with warnings)")

    return "\n".join(lines)


def _format_json_report(
    findings: list[Finding],
    current: dict[str, int],
) -> str:
    return json.dumps(
        {
            "tables_scanned": len(current),
            "regressions": [f.to_dict() for f in findings if f.level == "REGRESSION"],
            "warnings": [f.to_dict() for f in findings if f.level == "WARNING"],
            "current_counts": current,
        },
        indent=2,
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check omnidash_analytics table row counts against baseline",
    )
    parser.add_argument(
        "--baseline-path",
        type=Path,
        default=DEFAULT_BASELINE_PATH,
        help=f"Path to baseline JSON file (default: {DEFAULT_BASELINE_PATH})",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save current counts as the new baseline after checking",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output machine-readable JSON",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    password = _load_password()
    current = _fetch_row_counts(password)
    baseline = _load_baseline(args.baseline_path)

    findings = compare(baseline, current)

    if args.json_output:
        print(
            _format_json_report(findings, current),
        )
    else:
        print(
            _format_text_report(findings, current),
        )

    if args.save_baseline:
        _save_baseline(args.baseline_path, current)
        if not args.json_output:
            print(
                f"\nBaseline saved to {args.baseline_path}",
            )

    has_regressions = any(f.level == "REGRESSION" for f in findings)
    sys.exit(1 if has_regressions else 0)


if __name__ == "__main__":
    main()
