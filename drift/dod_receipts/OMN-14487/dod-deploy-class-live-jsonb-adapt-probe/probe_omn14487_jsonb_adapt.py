# ruff: noqa: INP001, S608, BLE001, T201
# INP001: this is a standalone probe script under a dod_receipts evidence
#   directory, not a package (matches the scripts/** convention elsewhere
#   in this repo). S608: SCRATCH_TABLE is a hardcoded module constant, not
#   external input. BLE001: broad except is intentional -- the probe must
#   capture and report ANY unexpected exception type as a FAIL rather than
#   letting it propagate and lose the RED/GREEN comparison. T201: this is a
#   CLI probe whose stdout IS the evidence artifact (see check_value).
"""Live deploy-class probe for OMN-14487 (omnibase_infra#2279).

Runs INSIDE the stability-test lane's live runtime container against the
REAL deployed Postgres (same driver/version/schema-shape the production
projection handler writes to), NOT a mock and NOT local pytest.

Proves the exact defect class + fix pattern from the PR:
  RED:   a raw list[dict] value handed straight to psycopg2 for a JSONB
         column crashes with "can't adapt type 'dict'" -- the live
         cid a7edc49a crash path.
  GREEN: the same value wrapped in psycopg2.extras.Json(...) inserts
         cleanly into the real deployed JSONB column and round-trips.

Uses a scratch table with the EXACT DDL shape of
projection_delegation_inference_response_text.recent_responses (read from
docker/migrations/forward/nodes/node_projection_delegation_inference_response/
0001_create_projection_delegation_inference_response_text.sql) so this
exercises the real column type/constraint, not an approximation. The real
table does not exist yet on this lane (migration not yet applied), so a
scratch table is created and dropped -- zero persistent state left behind.
"""

import os
import sys

import psycopg2
import psycopg2.extras

DSN = os.environ["OMNIBASE_INFRA_DB_URL"]  # env-read-ok: docker-exec probe
SCRATCH_TABLE = "_occ_probe_omn14487_recent_responses"

SAMPLE_ROWS = [
    {
        "correlation_id": "a7edc49a-0000-4000-8000-000000000000",
        "model_name": "probe-model",
        "task_type": "",
        "generated_text": "occ live-probe row",
        "prompt_tokens": 12,
        "completion_tokens": 34,
        "latency_ms": 56,
        "captured_at": "2026-07-13T00:00:00Z",
    }
]


def main() -> int:
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute(f"DROP TABLE IF EXISTS {SCRATCH_TABLE}")
        cur.execute(
            f"""
            CREATE TABLE {SCRATCH_TABLE} (
                singleton_key TEXT PRIMARY KEY,
                recent_responses JSONB NOT NULL DEFAULT '[]'::jsonb
                    CHECK (jsonb_typeof(recent_responses) = 'array')
            )
            """
        )
        conn.commit()

        # --- RED: raw list[dict], no Json wrapper (the pre-fix behavior) ---
        red_failed_as_expected = False
        red_error = ""
        try:
            cur.execute(
                f"INSERT INTO {SCRATCH_TABLE} (singleton_key, recent_responses) "
                "VALUES (%s, %s)",
                ("red", SAMPLE_ROWS),
            )
            conn.commit()
        except psycopg2.ProgrammingError as exc:
            conn.rollback()
            red_error = str(exc)
            red_failed_as_expected = "can't adapt type 'dict'" in red_error
        except Exception as exc:  # pragma: no cover - diagnostic path
            conn.rollback()
            red_error = f"WRONG EXCEPTION TYPE: {type(exc).__name__}: {exc}"

        # --- GREEN: same value wrapped in psycopg2.extras.Json(...) ---
        green_ok = False
        green_error = ""
        try:
            cur.execute(
                f"INSERT INTO {SCRATCH_TABLE} (singleton_key, recent_responses) "
                "VALUES (%s, %s)",
                ("green", psycopg2.extras.Json(SAMPLE_ROWS)),
            )
            conn.commit()
            cur.execute(
                f"SELECT recent_responses, jsonb_typeof(recent_responses) "
                f"FROM {SCRATCH_TABLE} WHERE singleton_key = 'green'"
            )
            row = cur.fetchone()
            green_ok = row is not None and row[1] == "array" and row[0] == SAMPLE_ROWS
        except Exception as exc:  # pragma: no cover - diagnostic path
            conn.rollback()
            green_error = f"{type(exc).__name__}: {exc}"

    finally:
        cur.execute(f"DROP TABLE IF EXISTS {SCRATCH_TABLE}")
        conn.commit()
        cur.close()
        conn.close()

    print(f"RED_FAILED_AS_EXPECTED={red_failed_as_expected}")
    print(f"RED_ERROR={red_error!r}")
    print(f"GREEN_OK={green_ok}")
    print(f"GREEN_ERROR={green_error!r}")

    ok = red_failed_as_expected and green_ok
    print("PROBE_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
