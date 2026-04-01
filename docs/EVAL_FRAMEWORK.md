# ONEX Baseline Evaluation Framework

**Ticket**: OMN-6784
**Status**: Implemented
**Last Updated**: 2026-04-01

## Overview

The ONEX Baseline Evaluation Framework provides quantitative A/B testing of the
ONEX pipeline's effectiveness. It runs identical development tasks with ONEX
features enabled (treatment) and disabled (baseline), then compares metrics to
measure the value ONEX adds.

## Architecture

```
┌─────────────────────┐     ┌─────────────────────┐
│   Eval Suite YAML   │     │  ServiceEvalRunner   │
│  (onex_change_ctrl) │────▶│  (omnibase_infra)    │
└─────────────────────┘     └──────────┬───────────┘
                                       │
                           ┌───────────┴───────────┐
                           │                       │
                    ┌──────▼──────┐         ┌──────▼──────┐
                    │  ONEX ON    │         │  ONEX OFF   │
                    │  (treatment)│         │  (baseline) │
                    └──────┬──────┘         └──────┬──────┘
                           │                       │
                           └───────────┬───────────┘
                                       │
                           ┌───────────▼───────────┐
                           │     Comparator        │
                           │ (onex_change_control) │
                           └───────────┬───────────┘
                                       │
                     ┌─────────────────┼─────────────────┐
                     │                 │                  │
              ┌──────▼──────┐  ┌──────▼──────┐  ┌───────▼───────┐
              │ ModelEval   │  │ Kafka Event  │  │  Regression   │
              │ Report      │  │ (completed)  │  │  Check        │
              └─────────────┘  └──────┬───────┘  └───────────────┘
                                      │
                           ┌──────────▼──────────┐
                           │  omnidash           │
                           │  /eval-results page │
                           └─────────────────────┘
```

## Components

### 1. Eval Suite (onex_change_control)

**File**: `eval_suites/standard_v1.yaml`

Defines 10 standardized eval tasks across 5 categories:
- 3 bug-fix tasks
- 2 refactor tasks
- 2 feature tasks
- 2 review tasks
- 1 documentation task

Each task specifies:
- `setup_commands`: Environment preparation
- `success_criteria`: Machine-checkable pass/fail conditions
- `expected_files_changed`: Files that should be modified
- `max_duration_seconds`: Timeout

### 2. Eval Models (onex_change_control)

| Model | Purpose |
|-------|---------|
| `ModelEvalTask` | Single eval task definition |
| `ModelEvalSuite` | Collection of tasks with versioning |
| `ModelEvalRun` | One task execution in one mode |
| `ModelEvalRunPair` | Paired ON/OFF comparison |
| `ModelEvalReport` | Full A/B report with summary |
| `ModelEvalSummary` | Aggregated statistics |

### 3. Suite Manager (onex_change_control)

**File**: `src/onex_change_control/eval/suite_manager.py`

- Load and validate suites from YAML
- Detect version changes via SHA-256 content hashing
- List available suites
- Track loaded suite hashes for change detection

### 4. Eval Runner (omnibase_infra)

**File**: `src/omnibase_infra/services/eval/service_eval_runner.py`

- Toggles `ENABLE_*` feature flags between modes
- Runs setup commands per task
- Checks machine-checkable success criteria
- Collects latency and success rate metrics
- Records git SHA and environment snapshot per run

### 5. Baseline Passthrough (omnibase_infra)

**File**: `src/omnibase_infra/services/eval/baseline_passthrough.py`

Documents and verifies ONEX_OFF behavior:
- All `ENABLE_*` flags disabled
- Events still flow through Kafka
- Baseline events tagged with `mode: baseline`

### 6. Comparator (onex_change_control)

**File**: `src/onex_change_control/eval/comparator.py`

- Pairs ON/OFF runs by task_id
- Computes per-metric deltas
- Determines verdicts using threshold rules:
  - Success rate: any change produces a verdict
  - Latency: >10% reduction = ONEX better
  - Token count: >5% reduction = ONEX better

### 7. Metric Collector (omnibase_infra)

**File**: `src/omnibase_infra/services/eval/metric_collector.py`

- Buffers Kafka events by correlation ID and time window
- Provides per-metric averages and summaries
- Used by the eval orchestrator during live runs

### 8. Eval Event Emitter (omnibase_infra)

**File**: `src/omnibase_infra/services/eval/eval_event_emitter.py`

- Topic: `onex.evt.onex-change-control.eval-completed.v1`
- Serializes ModelEvalReport to Kafka-compatible JSON

### 9. Regression Check (omnibase_infra)

**File**: `src/omnibase_infra/services/eval/eval_regression_check.py`

- Checks if ONEX is worse on >30% of tasks (configurable threshold)
- Returns structured `EvalRegressionResult`
- Integrated into close-out autopilot

### 10. Eval Orchestrator Skill (omniclaude)

**File**: `plugins/onex/skills/eval_orchestrator/SKILL.md`

Steps:
1. Load eval suite from YAML
2. Run A/B eval (both modes)
3. Generate report via comparator
4. Export to JSON + Markdown
5. Optionally emit Kafka event
6. Print summary

### 11. Dashboard (omnidash)

- **Page**: `/eval-results` — summary cards, latency chart, per-task table
- **Projection**: `EvalProjectionHandler` consumes eval-completed events
- **API**: `GET /api/eval-results/latest`, `GET /api/eval-results`
- **Migration**: `0049_eval_reports.sql`

## Running an Eval

### Via the eval orchestrator skill

```
/eval_orchestrator
```

### Programmatically

```python
from pathlib import Path
from onex_change_control.eval import SuiteManager, compute_eval_report
from omnibase_infra.services.eval.service_eval_runner import ServiceEvalRunner

# Load suite
manager = SuiteManager(Path("eval_suites"))
suite = manager.load_suite("standard_v1.yaml")

# Run A/B eval
runner = ServiceEvalRunner(workspace_root="/Volumes/PRO-G40/Code/omni_home")
on_runs, off_runs = runner.run_ab_suite(suite)

# Generate report
report = compute_eval_report(on_runs, off_runs, suite.suite_id, suite.version)
print(f"Better: {report.summary.onex_better_count}/{report.summary.total_tasks}")
```

## Metrics Collected

| Metric | Unit | Better when... |
|--------|------|----------------|
| `LATENCY_MS` | milliseconds | Lower (ONEX faster) |
| `TOKEN_COUNT` | count | Lower (ONEX uses fewer tokens) |
| `SUCCESS_RATE` | ratio 0-1 | Higher (ONEX succeeds more) |
| `PATTERN_HIT_RATE` | ratio 0-1 | Higher (patterns applied) |
| `ERROR_COUNT` | count | Lower |
| `RETRY_COUNT` | count | Lower |

## Verdicts

| Verdict | Meaning |
|---------|---------|
| `ONEX_BETTER` | ONEX improved outcomes |
| `ONEX_WORSE` | ONEX degraded outcomes |
| `NEUTRAL` | No significant difference |
| `INCOMPLETE` | Missing data for comparison |

## Regression Threshold

Default: ONEX worse on >30% of tasks triggers a regression alert.
Configurable via `check_eval_regression(report, threshold=0.30)`.
