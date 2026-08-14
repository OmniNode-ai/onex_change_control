# SPDX-License-Identifier: MIT
"""Validate docker-compose DATABASE_URL defaults against routing rules.

Parses ${VAR:-default} patterns from docker-compose files and verifies
that the default database name matches the expected database for each
service, based on service naming patterns.

Usage:
    uv run python -m onex_change_control.scripts.check_db_routing \
        --compose path/to/docker-compose.yml \
        --rules path/to/db_routing_rules.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Finding:
    check: str
    severity: str
    detail: str


@dataclass
class RoutingResult:
    ok: bool = True
    findings: list[Finding] = field(default_factory=list)

    def add(self, check: str, severity: str, detail: str) -> None:
        self.findings.append(Finding(check=check, severity=severity, detail=detail))
        if severity == "ERROR":
            self.ok = False


# Pattern to extract database name from postgresql:// connection strings
_DB_NAME_RE = re.compile(r"postgresql://[^/]+/(\w+)")

# Pattern to extract ${VAR:-default} values
_DEFAULT_RE = re.compile(r"\$\{[^:}]+:-([^}]+)\}")


def _extract_default_db(value: str) -> str | None:
    """Extract the database name from a DATABASE_URL default value."""
    m = _DEFAULT_RE.search(value)
    if not m:
        return None
    default_url = m.group(1)
    db_match = _DB_NAME_RE.search(default_url)
    return db_match.group(1) if db_match else None


def check_routing(compose_path: Path, rules_path: Path) -> RoutingResult:
    """Check compose service DATABASE_URL defaults against routing rules."""
    result = RoutingResult()

    compose_data = yaml.safe_load(compose_path.read_text())
    rules_data = yaml.safe_load(rules_path.read_text())

    services = compose_data.get("services", {})
    rules = rules_data.get("rules", [])

    for svc_name, svc_config in services.items():
        env = svc_config.get("environment", {})
        if isinstance(env, list):
            # Convert list format to dict
            env = dict(item.split("=", 1) for item in env if "=" in item)

        db_url = env.get("DATABASE_URL", "")
        if not db_url:
            continue

        default_db = _extract_default_db(str(db_url))
        if not default_db:
            continue

        for rule in rules:
            pattern = rule.get("service_pattern", "")
            expected_db = rule.get("expected_database", "")
            if (
                re.search(pattern, svc_name, re.IGNORECASE)
                and default_db != expected_db
            ):
                result.add(
                    "WRONG_DATABASE_DEFAULT",
                    "ERROR",
                    f"Service '{svc_name}' matches pattern '{pattern}' but "
                    f"DATABASE_URL defaults to '{default_db}' "
                    f"(expected '{expected_db}')",
                )

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate DB routing in compose files")
    parser.add_argument("--compose", type=Path, required=True)
    parser.add_argument(
        "--rules",
        type=Path,
        default=None,
        help="Path to db_routing_rules.yaml (default: auto-detect)",
    )
    args = parser.parse_args()

    rules = args.rules or (
        Path(__file__).parent.parent / "boundaries" / "db_routing_rules.yaml"
    )

    result = check_routing(args.compose, rules)

    for f in result.findings:
        print(f"[{f.severity}] {f.check}: {f.detail}")

    if not result.findings:
        print("DB routing: all service defaults match expected databases.")

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
