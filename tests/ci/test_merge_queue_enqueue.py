# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for the merge-queue enqueue decision logic (OMN-13214).

These tests pin the exact armed-but-not-enqueued failure modes observed in the
2026-06-17 dev wave:

* CLEAN + armed but isInMergeQueue=false  -> ENQUEUE (the core bug).
* CLEAN + already in queue                -> ALREADY_QUEUED (no double-enqueue).
* BEHIND on a strict-update repo (#1245)  -> UPDATE_BRANCH_THEN_ENQUEUE.
* BEHIND on a non-strict repo             -> ENQUEUE (GitHub queue updates it).
* BLOCKED / DIRTY / DRAFT / UNKNOWN       -> WAIT (not our bug).
* verify_enqueued reflects isInMergeQueue.
* error-message classification (no-queue / benign race).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "ci" / "merge_queue_enqueue.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("merge_queue_enqueue", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()
Action = mod.EnumEnqueueAction


def _pr(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "isInMergeQueue": False,
        "mergeStateStatus": "CLEAN",
        "autoMergeRequest": {"mergeMethod": "SQUASH", "enabledBy": {"login": "x"}},
        "mergeable": "MERGEABLE",
    }
    base.update(overrides)
    return base


class TestClassifyPr:
    def test_clean_armed_not_in_queue_enqueues(self) -> None:
        # The exact #1243/#1244/#1249 state: CLEAN + armed + queue empty.
        assert mod.classify_pr(_pr(), strict_update=False) is Action.ENQUEUE

    def test_clean_armed_not_in_queue_enqueues_strict(self) -> None:
        assert mod.classify_pr(_pr(), strict_update=True) is Action.ENQUEUE

    def test_already_in_queue_is_noop(self) -> None:
        pr = _pr(isInMergeQueue=True)
        assert mod.classify_pr(pr, strict_update=True) is Action.ALREADY_QUEUED

    def test_in_queue_takes_priority_over_behind(self) -> None:
        pr = _pr(isInMergeQueue=True, mergeStateStatus="BEHIND")
        assert mod.classify_pr(pr, strict_update=True) is Action.ALREADY_QUEUED

    def test_behind_strict_update_requires_branch_update(self) -> None:
        # The #1245 state: behind:3/4 on strict-update omnimarket.
        pr = _pr(mergeStateStatus="BEHIND", behind=3)
        assert (
            mod.classify_pr(pr, strict_update=True) is Action.UPDATE_BRANCH_THEN_ENQUEUE
        )

    def test_behind_non_strict_enqueues_directly(self) -> None:
        pr = _pr(mergeStateStatus="BEHIND", behind=3)
        assert mod.classify_pr(pr, strict_update=False) is Action.ENQUEUE

    def test_behind_integer_signal_without_state(self) -> None:
        pr = _pr(mergeStateStatus="CLEAN", behind=2)
        assert (
            mod.classify_pr(pr, strict_update=True) is Action.UPDATE_BRANCH_THEN_ENQUEUE
        )

    @pytest.mark.parametrize("state", ["BLOCKED", "DIRTY", "DRAFT", "UNKNOWN"])
    def test_non_enqueueable_states_wait(self, state: str) -> None:
        pr = _pr(mergeStateStatus=state)
        assert mod.classify_pr(pr, strict_update=False) is Action.WAIT

    @pytest.mark.parametrize("state", ["HAS_HOOKS", "UNSTABLE"])
    def test_mergeable_states_enqueue(self, state: str) -> None:
        pr = _pr(mergeStateStatus=state)
        assert mod.classify_pr(pr, strict_update=False) is Action.ENQUEUE

    def test_unrecognised_state_is_blocked_not_silently_enqueued(self) -> None:
        pr = _pr(mergeStateStatus="SOME_NEW_GITHUB_STATE")
        assert mod.classify_pr(pr, strict_update=False) is Action.BLOCKED

    def test_unarmed_clean_pr_still_enqueues(self) -> None:
        # Arming and enqueue are independent; the workflow arms first. classify
        # only decides enqueue eligibility from merge state, not armed-ness.
        pr = _pr(autoMergeRequest=None)
        assert mod.classify_pr(pr, strict_update=False) is Action.ENQUEUE


class TestArmedHelpers:
    def test_is_armed_true_for_object(self) -> None:
        assert mod.is_armed(_pr()) is True

    def test_is_armed_false_for_null(self) -> None:
        assert mod.is_armed(_pr(autoMergeRequest=None)) is False

    def test_is_armed_false_for_empty(self) -> None:
        assert mod.is_armed(_pr(autoMergeRequest={})) is False


class TestVerifyEnqueued:
    def test_verify_true_when_in_queue(self) -> None:
        assert mod.verify_enqueued({"isInMergeQueue": True}) is True

    def test_verify_false_when_not_in_queue(self) -> None:
        assert mod.verify_enqueued({"isInMergeQueue": False}) is False

    def test_verify_false_when_field_missing(self) -> None:
        assert mod.verify_enqueued({}) is False


class TestErrorClassification:
    @pytest.mark.parametrize(
        "msg",
        [
            "Repository does not have a merge queue",
            "merge queue is not enabled for this branch",
            "MERGE_QUEUE_NOT_ENABLED",
        ],
    )
    def test_no_queue_markers(self, msg: str) -> None:
        assert mod.is_no_merge_queue_error(msg) is True
        assert mod.is_benign_enqueue_error(msg) is False

    @pytest.mark.parametrize(
        "msg",
        [
            "Pull request is already enqueued",
            "PR is already queued",
            "pull request is in the merge queue",
        ],
    )
    def test_benign_markers(self, msg: str) -> None:
        assert mod.is_benign_enqueue_error(msg) is True
        assert mod.is_no_merge_queue_error(msg) is False

    def test_unrelated_error_is_neither(self) -> None:
        msg = "GraphQL: Something unexpected happened"
        assert mod.is_no_merge_queue_error(msg) is False
        assert mod.is_benign_enqueue_error(msg) is False


class TestCli:
    def test_classify_cli_prints_action(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = mod.main(
            ["classify", "--pr-json", json.dumps(_pr()), "--strict-update", "false"]
        )
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out == Action.ENQUEUE.value

    def test_classify_cli_strict_behind(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pr = _pr(mergeStateStatus="BEHIND", behind=1)
        rc = mod.main(
            ["classify", "--pr-json", json.dumps(pr), "--strict-update", "true"]
        )
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out == Action.UPDATE_BRANCH_THEN_ENQUEUE.value

    def test_verify_cli_exit_zero_in_queue(self) -> None:
        rc = mod.main(["verify", "--pr-json", json.dumps({"isInMergeQueue": True})])
        assert rc == 0

    def test_verify_cli_exit_one_not_in_queue(self) -> None:
        rc = mod.main(["verify", "--pr-json", json.dumps({"isInMergeQueue": False})])
        assert rc == 1
