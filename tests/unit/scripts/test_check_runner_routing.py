# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

from pathlib import Path

from scripts.validation.check_runner_routing import (
    FORK_AWARE_ROUTE,
    INDEX_ENV_VARS,
    PUBLIC_SELECTOR,
    TAILNET_SUFFIX,
    TRUSTED_SELECTOR,
    expected_selector,
    validate_index_env,
    validate_runs_on,
    validate_workflows,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# The exact shape this repo's ci.yml carried before OMN-16682: a devpi mirror
# selected by a ternary keyed on fork-vs-same-repo. On a same-repo PR it
# resolves to the tailnet host, which a GitHub-hosted runner cannot reach.
FORK_KEYED_DEVPI_TERNARY = """${{
  (
  github.event_name == 'pull_request'
  && github.event.pull_request.head.repo.full_name != github.repository
  )
  && 'https://pypi.org/simple/'
  || 'http://omninode-pc.tail75df5e.ts.net:3141/root/pypi/+simple/'
}}"""


def test_normal_route_selects_public_only_for_fork_pull_requests() -> None:
    repository = "OmniNode-ai/onex_change_control"

    assert expected_selector("pull_request", repository, repository) == "trusted"
    assert (
        expected_selector("pull_request", "contributor/onex_change_control", repository)
        == "public"
    )
    assert expected_selector("push", None, repository) == "trusted"
    assert expected_selector("merge_group", None, repository) == "trusted"


def test_workflows_use_the_fork_aware_route() -> None:
    assert validate_workflows(REPO_ROOT) == []


def test_repository_does_not_declare_pull_request_target() -> None:
    workflow_sources = (REPO_ROOT / ".github/workflows").glob("*.yml")

    assert all(
        "  pull_request_target:" not in workflow.read_text(encoding="utf-8")
        for workflow in workflow_sources
    )


def test_legacy_dev_base_public_runner_shortcut_is_rejected() -> None:
    legacy_route = f"""(github.event_name == 'pull_request' && github.base_ref == 'dev')
&& {PUBLIC_SELECTOR}
|| {FORK_AWARE_ROUTE}"""

    violations = validate_runs_on(
        workflow_path=".github/workflows/example.yml",
        job_name="example",
        runs_on=legacy_route,
    )

    assert violations == [
        ".github/workflows/example.yml:example uses the forbidden dev-base "
        "public-runner shortcut"
    ]


def test_pr_wide_public_runner_route_is_rejected() -> None:
    pr_wide_route = f"""github.event_name == 'pull_request'
&& {PUBLIC_SELECTOR}
|| {TRUSTED_SELECTOR}"""

    violations = validate_runs_on(
        workflow_path=".github/workflows/example.yml",
        job_name="example",
        runs_on=pr_wide_route,
    )

    assert violations == [
        ".github/workflows/example.yml:example must route only fork pull requests "
        "to the public selector"
    ]


def test_fork_keyed_devpi_ternary_is_rejected_for_every_index_env_var() -> None:
    """OMN-16682: the pre-fix shape must be rejected, whichever var carries it.

    This is the red half of the red/green proof for the ci.yml change: the
    ternary reads as "public index on fork PRs", but a fork PR is not the only
    way a job lands on a GitHub-hosted runner -- flipping
    OMNI_TRUSTED_CI_RUNS_ON_JSON does it for same-repo PRs too, and then this
    expression hands that hosted runner a tailnet-only index.
    """
    for var in INDEX_ENV_VARS:
        violations = validate_index_env(
            workflow_path=".github/workflows/ci.yml",
            job_name="validate-prod-promotion-grants",
            env={var: FORK_KEYED_DEVPI_TERNARY},
        )

        assert len(violations) == 1, f"{var} was not rejected"
        assert var in violations[0]
        assert TAILNET_SUFFIX in violations[0]


def test_literal_tailnet_index_is_rejected_without_any_ternary() -> None:
    """A bare pin is the same defect with the guard removed."""
    violations = validate_index_env(
        workflow_path=".github/workflows/ci.yml",
        job_name="example",
        env={
            "UV_DEFAULT_INDEX": (
                "http://omninode-pc.tail75df5e.ts.net:3141/root/pypi/+simple/"
            )
        },
    )

    assert len(violations) == 1


def test_public_index_and_unrelated_env_vars_are_allowed() -> None:
    violations = validate_index_env(
        workflow_path=".github/workflows/ci.yml",
        job_name="example",
        env={
            "UV_DEFAULT_INDEX": "https://pypi.org/simple/",
            "PIP_EXTRA_INDEX_URL": "https://pypi.org/simple/",
            "UV_INDEX_STRATEGY": "unsafe-best-match",
            # Not an index var: the tailnet host is legitimate here.
            "OMNI_RUNTIME_HOST": "omninode-pc.tail75df5e.ts.net",
        },
    )

    assert violations == []


def test_missing_or_non_mapping_env_is_not_a_violation() -> None:
    envs: tuple[object, ...] = (None, "not-a-mapping", [])
    for env in envs:
        assert (
            validate_index_env(
                workflow_path=".github/workflows/ci.yml",
                job_name="example",
                env=env,
            )
            == []
        )


def test_step_level_index_pin_is_caught_too(tmp_path: Path) -> None:
    """A step env pins the index just as effectively as a job env does."""
    workflow_dir = tmp_path / ".github/workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "example.yml").write_text(
        "name: example\n"
        "on: [push]\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: install\n"
        "        env:\n"
        "          PIP_INDEX_URL: "
        '"http://omninode-pc.tail75df5e.ts.net:3141/root/pypi/+simple/"\n'
        "        run: pip install -e .\n",
        encoding="utf-8",
    )

    violations = validate_workflows(tmp_path)

    assert len(violations) == 1
    assert "PIP_INDEX_URL" in violations[0]
