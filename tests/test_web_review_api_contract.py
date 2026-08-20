from __future__ import annotations

import http.client
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import numpy as np

from _fixtures import spectrum_lines, write_ms_file
from ms_event_studio.demo import create_guided_source
from ms_event_studio.errors import WorkspaceRequestError
from ms_event_studio.project import CreateProjectRequest, create_project
from ms_event_studio.scientific_settings import ProjectScientificSettings
from ms_event_studio.web_app import WebBoundaryError, WebSession, create_http_server
from ms_event_studio.web_review_service import BrowserWorkspaceService
from ms_event_studio.window_service import ProjectWindowService


def create_guided_project(root: Path, *, add_manual: bool = False):
    source = root / "guided-source.txt"
    create_guided_source(source)
    project = create_project(
        CreateProjectRequest(
            source_path=source,
            project_dir=root / "guided-project",
            display_name="浏览器审阅项目",
            analysis_start_min="0",
            analysis_end_min="2",
        )
    )
    if add_manual:
        with ProjectWindowService.open(project.project_dir) as service:
            service.review_store.add_event(
                click_time_sec=45.0,
                scans=service.scans,
                analysis_start_ns=service.analysis_start_ns,
                analysis_end_ns=service.analysis_end_ns,
                actor="fixture",
                session_id="fixture",
                reason="低强度遗漏峰",
            )
    return source, project


def create_adjustable_project(
    root: Path,
    *,
    adjust: bool = True,
    collision_gap_sec: float = 0.6,
):
    count = 1201
    positions = np.arange(count, dtype=float)
    signal = 1000.0 * np.exp(-0.5 * ((positions - 300.0) / 30.0) ** 2)
    # This small local maximum lies inside the broad immutable automatic
    # support but below the detector's calling prominence.  ReviewStore can
    # therefore relocate to real scan evidence without introducing a second
    # automatic event.
    signal[309] -= 10.0
    signal[310] += 10.0
    signal[311] -= 10.0
    signal[600] = 1200.0
    signal[900] = 1100.0
    source = write_ms_file(
        root / "adjustable-source.txt",
        [
            spectrum_lines(
                index,
                index + 1,
                f"{index / 600.0:.12f}",
                intensities=[0.0, float(signal[index]), 10.0, 0.0],
            )
            for index in range(count)
        ],
    )
    project = create_project(
        CreateProjectRequest(
            source_path=source,
            project_dir=root / "adjustable-project",
            display_name="可恢复峰顶项目",
            analysis_start_min="0",
            analysis_end_min="2",
            scientific_settings=ProjectScientificSettings(
                collision_gap_sec=collision_gap_sec,
            ),
        )
    )
    if adjust:
        with ProjectWindowService.open(project.project_dir) as service:
            event = min(
                service.all_events(),
                key=lambda row: abs(int(row["current_spectrum_index"]) - 300),
            )
            accepted = service.review_store.set_status(
                event["event_id"],
                "accepted",
                expected_revision=int(event["revision"]),
                actor="fixture",
                session_id="fixture",
                reason="确认真实峰",
            )
            adjusted = service.review_store.adjust_apex(
                event["event_id"],
                click_time_sec=31.0,
                scans=service.scans,
                analysis_start_ns=service.analysis_start_ns,
                analysis_end_ns=service.analysis_end_ns,
                expected_revision=int(accepted["revision"]),
                actor="fixture",
                session_id="fixture",
                reason="调整到相邻局部峰",
            )
            if int(adjusted["current_spectrum_index"]) != 310:
                raise AssertionError("adjustable fixture did not snap to its intended real scan")
    return source, project


def open_session(root: Path, project) -> WebSession:
    session = WebSession(root / "recent.json")
    selection = session.register_path("project_open", project.project_dir)
    session.open_project(selection["selection_token"])
    return session


def recursive_keys(value: object) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            keys.append(str(key))
            keys.extend(recursive_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.extend(recursive_keys(child))
    return keys


def assert_workspace_safe(
    test: unittest.TestCase,
    payload: object,
    *,
    private_values: tuple[str, ...] = (),
) -> None:
    forbidden = {
        "path",
        "event_id",
        "revision",
        "schema",
        "sqlite",
        "manifest",
        "snapshot",
        "bucket",
        "source_sha256",
        "generation_id",
        "project_id",
        "scan_row_index",
        "spectrum_index",
        "auto_event_id",
    }
    keys = recursive_keys(payload)
    for key in keys:
        test.assertNotIn(key.casefold(), forbidden)
        test.assertFalse(key.casefold().endswith("_ns"), key)
    serialized = json.dumps(payload, ensure_ascii=False)
    for value in private_values:
        test.assertNotIn(value, serialized)
    for term in ("EventID", "revision", "SQLite", "manifest", "generation_id"):
        test.assertNotIn(term, serialized)


class WorkspaceReadContractTest(unittest.TestCase):
    def test_workspace_is_structured_selects_first_unreviewed_and_separates_overlay(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source, project = create_guided_project(root, add_manual=True)
            internal_ids: tuple[str, ...]
            with ProjectWindowService.open(project.project_dir) as service:
                internal_ids = tuple(str(row["event_id"]) for row in service.all_events())
            session = open_session(root, project)
            try:
                workspace = session.workspace()
                self.assertEqual(
                    list(workspace),
                    ["project", "review", "filters", "events", "selection", "window", "history"],
                )
                self.assertEqual(workspace["review"], {
                    "total": 4,
                    "reviewed": 1,
                    "unreviewed": 3,
                    "accepted": 1,
                    "rejected": 0,
                    "pending": 0,
                })
                selected = workspace["selection"]
                self.assertEqual(selected["event"]["sequence"], 1)
                self.assertEqual(selected["event"]["status"], "unreviewed")
                self.assertEqual(selected["core_evidence"]["measured_mz"], 760.5851)
                self.assertIn("quality", selected["core_evidence"])
                self.assertIn("adjustment_range", selected["more_evidence"])
                self.assertEqual(
                    [row["value"] for row in workspace["filters"]],
                    [
                        "all",
                        "unreviewed",
                        "accepted",
                        "rejected",
                        "pending",
                        "manual_added",
                        "manual_adjusted",
                    ],
                )
                self.assertTrue(
                    {row["marker"]["code"] for row in workspace["events"]}
                    <= {"U", "A", "R", "P"}
                )

                narrowed = session.workspace(
                    {
                        # The browser emits finite JSON numbers; Python still
                        # converts them once to exact integer nanoseconds.
                        "start_min": 0.4,
                        "end_min": 1.1,
                        "point_budget": 64,
                        "status_filter": "accepted",
                        "selected_event_token": workspace["events"][1]["event_token"],
                        "maximum_labels": 8,
                    }
                )
                trace = narrowed["window"]["trace"]
                self.assertGreater(len(trace), 2)
                self.assertLess(len(trace), 2 * 64 + 1)
                self.assertEqual(
                    [point["time_min"] for point in trace],
                    sorted(point["time_min"] for point in trace),
                )
                overlay = narrowed["window"]["event_overlay"]
                self.assertEqual(len(overlay), 1)
                self.assertEqual(overlay[0]["origin"], "manual_added")
                overlay_tokens = {row["event_token"] for row in overlay}
                self.assertTrue(set(narrowed["window"]["label_event_tokens"]).issubset(overlay_tokens))
                self.assertNotEqual(len(trace), len(overlay))
                shifted = session.workspace(
                    {
                        "start_min": 0.5,
                        "end_min": 1.2,
                        "selected_event_token": None,
                    }
                )
                self.assertEqual(
                    shifted["selection"]["event"]["event_token"],
                    narrowed["selection"]["event"]["event_token"],
                )
                assert_workspace_safe(
                    self,
                    shifted,
                    private_values=(str(root), str(source), str(project.project_dir), *internal_ids),
                )
            finally:
                session.close()

    def test_window_inputs_are_closed_and_revalidated_by_python(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root)
            session = open_session(root, project)
            try:
                for payload in (
                    {"start_min": "0", "end_min": "3"},
                    {"start_min": 0, "end_min": 0},
                    {"start_min": "0"},
                    {"point_budget": True},
                    {"status_filter": "stale"},
                    {"selected_event_token": "invented-token"},
                    {"raw_event_id": "EV_private"},
                ):
                    with self.subTest(payload=payload):
                        with self.assertRaises(WebBoundaryError):
                            session.workspace(payload)
            finally:
                session.close()


class ReviewWriteContractTest(unittest.TestCase):
    def test_bulk_accept_skips_collision_risk_and_is_one_history_step(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root)
            service = BrowserWorkspaceService(project)
            try:
                automatic = service._window_service.automatic
                automatic.loc[:, "collision_risk_high"] = False
                automatic.loc[automatic.index[0], "collision_risk_high"] = True

                initial = service.workspace()
                self.assertEqual(
                    initial["window"]["bulk_review"],
                    {
                        "eligible_count": 2,
                        "skipped_count": 1,
                        "original_risk_count": 1,
                        "current_risk_count": 0,
                    },
                )
                first = initial["events"][0]
                self.assertTrue(first["original_auto_collision_risk"])
                self.assertFalse(first["current_apex_collision_risk"])
                saved = service.bulk_accept_visible(
                    {"confirmed": True, "note": "批量确认自动识别良好的峰"}
                )
                self.assertEqual(saved["workspace"]["review"]["accepted"], 2)
                self.assertEqual(saved["workspace"]["review"]["unreviewed"], 1)
                self.assertIn("已跳过 1 个与相邻事件距离过近的事件", saved["message"])
                self.assertIn(
                    "自动识别时相邻事件距离较近",
                    saved["workspace"]["selection"]["core_evidence"]["quality"]["notes"],
                )
                audit = service._window_service.review_store.audit_events()
                self.assertEqual(
                    audit[0]["details"],
                    {
                        "event_count": 2,
                        "bulk_policy": "original_or_current_collision_risk_v1",
                        "collision_gap_sec": 0.6,
                        "skipped_count": 1,
                        "original_risk_count": 1,
                        "current_risk_count": 0,
                    },
                )

                undone = service.undo({})["workspace"]
                self.assertEqual(undone["review"]["accepted"], 0)
                self.assertEqual(undone["review"]["unreviewed"], 3)
                self.assertTrue(undone["history"]["can_redo"])

                redone = service.redo({})["workspace"]
                self.assertEqual(redone["review"]["accepted"], 2)
                self.assertEqual(redone["review"]["unreviewed"], 1)
                no_change = service.bulk_accept_visible({"confirmed": True})
                self.assertIn(
                    "1 个未审阅事件都与相邻事件距离过近，未作修改",
                    no_change["message"],
                )
                self.assertEqual(no_change["workspace"]["review"], redone["review"])
            finally:
                service.close()

    def test_live_collision_risk_is_global_strict_and_combined_with_original(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root)
            service = BrowserWorkspaceService(project)
            try:
                service._window_service.automatic.loc[:, "collision_risk_high"] = False
                active = service._active_events(service._window_service.all_events())
                base_ns = 30_000_000_000
                active[0]["current_apex_time_ns"] = base_ns
                active[0]["current_apex_time_sec"] = 30.0
                active[0]["status"] = "accepted"
                active[1]["current_apex_time_ns"] = base_ns + 590_000_000
                active[1]["current_apex_time_sec"] = 30.59
                # Exactly 0.60 s from the second event: strict '<' must not
                # make this third event risky.
                active[2]["current_apex_time_ns"] = base_ns + 1_190_000_000
                active[2]["current_apex_time_sec"] = 31.19

                risks = service._collision_risks(active)
                first, second, third = [risks[str(row["event_id"])] for row in active]
                self.assertTrue(first.current_apex)
                self.assertTrue(second.current_apex)
                self.assertFalse(third.current_apex)
                self.assertFalse(second.original_auto)

                # The accepted first event is hidden from the candidate set,
                # but it must still protect its unreviewed neighbour.
                bulk = service._bulk_review_candidates(active, risks)
                self.assertEqual(
                    [str(row["event_id"]) for row in bulk.skipped],
                    [str(active[1]["event_id"])],
                )
                self.assertEqual(
                    [str(row["event_id"]) for row in bulk.eligible],
                    [str(active[2]["event_id"])],
                )
                self.assertEqual(bulk.original_risk_count, 0)
                self.assertEqual(bulk.current_risk_count, 1)

                stale_first = dict(active[0], generation_state="stale")
                without_stale = service._collision_risks(
                    [stale_first, active[1], active[2]]
                )
                self.assertFalse(without_stale[str(active[1]["event_id"])].current_apex)
                self.assertFalse(without_stale[str(active[2]["event_id"])].current_apex)

                manual = dict(active[2])
                manual["event_id"] = "manual-test-event"
                manual["origin"] = "manual_added"
                manual["original_auto_event_id"] = None
                manual["auto_event_id"] = None
                manual["current_apex_time_ns"] = base_ns + 3_000_000_000
                manual_risk = service._collision_risks([manual])["manual-test-event"]
                self.assertIsNone(manual_risk.original_auto)
                self.assertFalse(manual_risk.current_apex)
            finally:
                service.close()

    def test_missing_original_collision_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root)
            service = BrowserWorkspaceService(project)
            try:
                service._window_service.automatic["collision_risk_high"] = "False"
                with self.assertRaisesRegex(
                    WorkspaceRequestError,
                    "原始相邻风险证据无效",
                ):
                    service.workspace()
                service._window_service.automatic.drop(
                    columns=["collision_risk_high"],
                    inplace=True,
                )
                with self.assertRaisesRegex(
                    WorkspaceRequestError,
                    "原始相邻风险证据不完整",
                ):
                    service.workspace()
            finally:
                service.close()

    def test_adjust_undo_redo_reopen_refreshes_live_risk_and_bulk_gate(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_adjustable_project(
                root,
                adjust=False,
                collision_gap_sec=30.0,
            )
            session = open_session(root, project)
            try:
                initial = session.workspace()
                first = min(
                    initial["events"],
                    key=lambda row: abs(float(row["apex_time_sec"]) - 30.0),
                )
                self.assertFalse(first["original_auto_collision_risk"])
                self.assertFalse(first["current_apex_collision_risk"])
                selected = session.workspace({"selected_event_token": first["event_token"]})
                aim = session.begin_event_edit(
                    {
                        "mode": "adjust",
                        "action_token": selected["selection"]["event"]["action_token"],
                    }
                )
                preview = session.preview_event_edit(
                    {"aim_token": aim["aim_token"], "click_time_min": 31.0 / 60.0}
                )
                adjusted = session.apply_event_edit(
                    {"preview_token": preview["preview_token"], "note": "形成近邻测试"}
                )["workspace"]
                self.assertTrue(
                    adjusted["selection"]["event"]["current_apex_collision_risk"]
                )
                self.assertFalse(
                    adjusted["selection"]["event"]["original_auto_collision_risk"]
                )

                undone = session.undo_review({})["workspace"]
                self.assertFalse(
                    undone["selection"]["event"]["current_apex_collision_risk"]
                )
                redone = session.redo_review({})["workspace"]
                self.assertTrue(
                    redone["selection"]["event"]["current_apex_collision_risk"]
                )
            finally:
                session.close()

            reopened = open_session(root, project)
            try:
                persisted = reopened.workspace()
                adjusted_event = min(
                    persisted["events"],
                    key=lambda row: abs(float(row["apex_time_sec"]) - 31.0),
                )
                self.assertTrue(adjusted_event["current_apex_collision_risk"])
                self.assertEqual(
                    persisted["window"]["bulk_review"],
                    {
                        "eligible_count": 1,
                        "skipped_count": 2,
                        "original_risk_count": 0,
                        "current_risk_count": 2,
                    },
                )
                saved = reopened.bulk_accept_visible({"confirmed": True})
                self.assertEqual(saved["workspace"]["review"]["accepted"], 1)
                self.assertEqual(saved["workspace"]["review"]["unreviewed"], 2)
            finally:
                reopened.close()

    def test_u_a_r_p_notes_auto_advance_undo_redo_and_reopen(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root, add_manual=True)
            session = open_session(root, project)
            try:
                initial = session.workspace()
                first_token = initial["selection"]["event"]["event_token"]
                first_action = initial["selection"]["event"]["action_token"]
                kept = session.review_decision(
                    {"action_token": first_action, "decision": "keep", "note": "峰形明确"}
                )["workspace"]
                self.assertNotEqual(kept["selection"]["event"]["event_token"], first_token)
                self.assertEqual(kept["review"]["accepted"], 2)
                with self.assertRaises(WebBoundaryError) as stale:
                    session.review_decision(
                        {"action_token": first_action, "decision": "exclude", "note": "旧操作"}
                    )
                self.assertEqual(stale.exception.code, "stale_action")

                pending = session.review_decision(
                    {
                        "action_token": kept["selection"]["event"]["action_token"],
                        "decision": "pending",
                        "note": "稍后复核",
                    }
                )["workspace"]
                excluded = session.review_decision(
                    {
                        "action_token": pending["selection"]["event"]["action_token"],
                        "decision": "exclude",
                        "note": "背景干扰",
                    }
                )["workspace"]
                first = next(row for row in excluded["events"] if row["event_token"] == first_token)
                selected_first = session.workspace(
                    {"selected_event_token": first_token, "status_filter": "all"}
                )
                first = selected_first["selection"]["event"]
                cleared = session.review_decision(
                    {
                        "action_token": first["action_token"],
                        "decision": "clear",
                        "note": "清除结论但保留峰位",
                    }
                )["workspace"]
                self.assertEqual(cleared["review"], {
                    "total": 4,
                    "reviewed": 3,
                    "unreviewed": 1,
                    "accepted": 1,
                    "rejected": 1,
                    "pending": 1,
                })
                self.assertEqual(cleared["selection"]["event"]["status"], "unreviewed")
                self.assertTrue(cleared["history"]["can_undo"])

                undone = session.undo_review({"note": "撤销清除"})["workspace"]
                self.assertEqual(undone["selection"]["event"]["status"], "accepted")
                self.assertTrue(undone["history"]["can_redo"])
                redone = session.redo_review({"note": "重做清除"})["workspace"]
                self.assertEqual(redone["selection"]["event"]["status"], "unreviewed")
                assert_workspace_safe(self, redone, private_values=(str(root), str(project.project_dir)))
            finally:
                session.close()

            reopened = open_session(root, project)
            try:
                persisted = reopened.workspace()
                self.assertEqual(persisted["review"], cleared["review"])
                self.assertEqual(persisted["selection"]["event"]["status"], "unreviewed")
                with ProjectWindowService.open(project.project_dir) as service:
                    audits = service.review_store.audit_events()
                reasons = [row["reason"] for row in audits]
                for expected in (
                    "峰形明确",
                    "稍后复核",
                    "背景干扰",
                    "清除结论但保留峰位",
                    "撤销清除",
                    "重做清除",
                ):
                    self.assertIn(expected, reasons)
                self.assertEqual(
                    [row["action"] for row in audits[-6:]],
                    ["set_status", "set_status", "set_status", "set_status", "undo", "redo"],
                )
            finally:
                reopened.close()

    def test_restore_automatic_apex_preserves_status_and_survives_history_reopen(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_adjustable_project(root)
            session = open_session(root, project)
            competing = open_session(root, project)
            try:
                initial = session.workspace()
                adjusted = next(row for row in initial["events"] if row["apex_modified"])
                selected = session.workspace({"selected_event_token": adjusted["event_token"]})
                before = selected["selection"]["event"]
                self.assertEqual(before["status"], "accepted")
                self.assertEqual(before["origin"], "manual_adjusted")
                self.assertTrue(before["can_restore_automatic_apex"])
                competing_initial = competing.workspace()
                competing_adjusted = next(
                    row for row in competing_initial["events"] if row["apex_modified"]
                )

                restored = session.restore_automatic_apex(
                    {"action_token": before["action_token"], "note": "回到自动定位"}
                )["workspace"]
                after = restored["selection"]["event"]
                self.assertEqual(after["status"], "accepted")
                self.assertEqual(after["origin"], "automatic")
                self.assertAlmostEqual(after["apex_time_sec"], 30.0)
                self.assertFalse(after["can_restore_automatic_apex"])
                with self.assertRaises(WebBoundaryError) as conflict:
                    competing.restore_automatic_apex(
                        {
                            "action_token": competing_adjusted["action_token"],
                            "note": "并发旧窗口不应覆盖",
                        }
                    )
                self.assertEqual(conflict.exception.code, "review_conflict")
                competing_refreshed = competing.workspace(
                    {"selected_event_token": competing_adjusted["event_token"]}
                )
                self.assertEqual(
                    competing_refreshed["selection"]["event"]["status"],
                    "accepted",
                )
                self.assertEqual(
                    competing_refreshed["selection"]["event"]["origin"],
                    "automatic",
                )

                undone = session.undo_review({"note": "撤销恢复"})["workspace"]
                self.assertEqual(undone["selection"]["event"]["status"], "accepted")
                self.assertEqual(undone["selection"]["event"]["origin"], "manual_adjusted")
                self.assertAlmostEqual(undone["selection"]["event"]["apex_time_sec"], 31.0)
                redone = session.redo_review({"note": "重做恢复"})["workspace"]
                self.assertEqual(redone["selection"]["event"]["status"], "accepted")
                self.assertEqual(redone["selection"]["event"]["origin"], "automatic")
            finally:
                competing.close()
                session.close()

            reopened = open_session(root, project)
            try:
                persisted = reopened.workspace()
                restored = next(
                    row for row in persisted["events"] if abs(row["apex_time_sec"] - 30.0) < 1e-8
                )
                self.assertEqual(restored["status"], "accepted")
                self.assertEqual(restored["origin"], "automatic")
                with ProjectWindowService.open(project.project_dir) as service:
                    actions = [row["action"] for row in service.review_store.audit_events()]
                    history = service.review_store.history_state()
                self.assertIn("restore_automatic_apex", actions)
                self.assertEqual(actions[-2:], ["undo", "redo"])
                self.assertEqual(history, {"can_undo": True, "can_redo": False})
            finally:
                reopened.close()

    def test_clear_review_preserves_the_adjusted_apex(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_adjustable_project(root)
            session = open_session(root, project)
            try:
                initial = session.workspace()
                adjusted = next(row for row in initial["events"] if row["apex_modified"])
                cleared = session.review_decision(
                    {
                        "action_token": adjusted["action_token"],
                        "decision": "clear",
                        "note": "只清除审阅结论",
                    }
                )["workspace"]
                same_event = next(
                    row for row in cleared["events"] if row["event_token"] == adjusted["event_token"]
                )
                self.assertEqual(same_event["status"], "unreviewed")
                self.assertEqual(same_event["origin"], "manual_adjusted")
                self.assertAlmostEqual(same_event["apex_time_sec"], 31.0)
                self.assertTrue(same_event["can_restore_automatic_apex"])

                undone = session.undo_review({"note": "撤销清除"})["workspace"]
                restored = next(
                    row for row in undone["events"] if row["event_token"] == adjusted["event_token"]
                )
                self.assertEqual(restored["status"], "accepted")
                self.assertAlmostEqual(restored["apex_time_sec"], 31.0)
            finally:
                session.close()

    def test_manual_event_cannot_use_restore_automatic_action(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root, add_manual=True)
            session = open_session(root, project)
            try:
                workspace = session.workspace()
                manual = next(row for row in workspace["events"] if row["origin"] == "manual_added")
                before = workspace["review"]
                with self.assertRaises(WebBoundaryError) as caught:
                    session.restore_automatic_apex(
                        {"action_token": manual["action_token"], "note": "不应允许"}
                    )
                self.assertEqual(caught.exception.code, "invalid_review_action")
                self.assertEqual(session.workspace()["review"], before)
            finally:
                session.close()

    def test_concurrent_conflict_and_injected_failure_leave_committed_state_intact(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root)
            session = open_session(root, project)
            try:
                initial = session.workspace()
                first = initial["selection"]["event"]
                with ProjectWindowService.open(project.project_dir) as concurrent:
                    backend = concurrent.all_events()[0]
                    concurrent.review_store.set_status(
                        backend["event_id"],
                        "accepted",
                        expected_revision=int(backend["revision"]),
                        actor="other-window",
                        session_id="other-window",
                        reason="并发提交",
                    )
                with self.assertRaises(WebBoundaryError) as conflict:
                    session.review_decision(
                        {
                            "action_token": first["action_token"],
                            "decision": "exclude",
                            "note": "不应覆盖并发结果",
                        }
                    )
                self.assertEqual(conflict.exception.code, "review_conflict")
                self.assertEqual(conflict.exception.status.value, 409)
                refreshed = session.workspace({"selected_event_token": first["event_token"]})
                self.assertEqual(refreshed["selection"]["event"]["status"], "accepted")

                action = refreshed["selection"]["event"]["action_token"]
                workspace_service = session._workspace
                assert workspace_service is not None
                store = workspace_service._window_service.review_store
                with patch.object(store, "set_status", side_effect=OSError("private disk failure")):
                    with self.assertRaises(OSError):
                        session.review_decision(
                            {"action_token": action, "decision": "pending", "note": "失败注入"}
                        )
                after_failure = session.workspace({"selected_event_token": first["event_token"]})
                self.assertEqual(after_failure["selection"]["event"]["status"], "accepted")
                with ProjectWindowService.open(project.project_dir) as verify:
                    audits = verify.review_store.audit_events()
                self.assertEqual([row["reason"] for row in audits], ["并发提交"])
            finally:
                session.close()


class ReviewHTTPContractTest(unittest.TestCase):
    @staticmethod
    def request(server, method: str, path: str, *, payload=None, token=None):
        parsed = urlparse(server.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["X-MS-Event-Token"] = token
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw)

    def test_http_write_token_closed_payload_history_and_reopen(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source, project = create_guided_project(root)
            session = open_session(root, project)
            server = create_http_server(session=session)
            try:
                server.start()
                status, bootstrap = self.request(server, "GET", "/api/bootstrap")
                self.assertEqual(status, 200)
                request_token = bootstrap["request_token"]
                status, workspace = self.request(server, "GET", "/api/workspace")
                self.assertEqual(status, 200)
                action = workspace["selection"]["event"]["action_token"]

                status, error = self.request(
                    server,
                    "POST",
                    "/api/review/decision",
                    payload={"action_token": action, "decision": "keep", "note": "HTTP 保留"},
                )
                self.assertEqual(status, 403)
                self.assertEqual(error["error"]["code"], "invalid_request_token")
                status, error = self.request(
                    server,
                    "POST",
                    "/api/workspace/window",
                    payload={"raw_event_id": "EV_private"},
                    token=request_token,
                )
                self.assertEqual(status, 400)

                status, saved = self.request(
                    server,
                    "POST",
                    "/api/review/decision",
                    payload={"action_token": action, "decision": "keep", "note": "HTTP 保留"},
                    token=request_token,
                )
                self.assertEqual(status, 200)
                self.assertTrue(saved["ok"])
                self.assertTrue(saved["workspace"]["history"]["can_undo"])
                assert_workspace_safe(
                    self,
                    saved,
                    private_values=(str(root), str(source), str(project.project_dir)),
                )
                status, undone = self.request(
                    server,
                    "POST",
                    "/api/review/undo",
                    payload={"note": "HTTP 撤销"},
                    token=request_token,
                )
                self.assertEqual(status, 200)
                self.assertTrue(undone["workspace"]["history"]["can_redo"])
                status, redone = self.request(
                    server,
                    "POST",
                    "/api/review/redo",
                    payload={"note": "HTTP 重做"},
                    token=request_token,
                )
                self.assertEqual(status, 200)
                self.assertEqual(redone["workspace"]["review"]["accepted"], 1)
            finally:
                server.stop()

            reopened_session = open_session(root, project)
            reopened_server = create_http_server(session=reopened_session)
            try:
                reopened_server.start()
                status, persisted = self.request(reopened_server, "GET", "/api/workspace")
                self.assertEqual(status, 200)
                self.assertEqual(persisted["review"]["accepted"], 1)
                assert_workspace_safe(
                    self,
                    persisted,
                    private_values=(str(root), str(source), str(project.project_dir)),
                )
            finally:
                reopened_server.stop()

    def test_http_conflict_and_failure_are_safe_and_do_not_overwrite_state(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source, project = create_guided_project(root)
            session = open_session(root, project)
            server = create_http_server(session=session)
            try:
                server.start()
                status, bootstrap = self.request(server, "GET", "/api/bootstrap")
                self.assertEqual(status, 200)
                request_token = bootstrap["request_token"]
                status, initial = self.request(server, "GET", "/api/workspace")
                self.assertEqual(status, 200)
                selected = initial["selection"]["event"]

                with ProjectWindowService.open(project.project_dir) as concurrent:
                    backend = concurrent.all_events()[0]
                    concurrent.review_store.set_status(
                        backend["event_id"],
                        "accepted",
                        expected_revision=int(backend["revision"]),
                        actor="other-window",
                        session_id="other-window",
                        reason="HTTP 并发提交",
                    )
                status, conflict = self.request(
                    server,
                    "POST",
                    "/api/review/decision",
                    payload={
                        "action_token": selected["action_token"],
                        "decision": "exclude",
                        "note": "不应覆盖",
                    },
                    token=request_token,
                )
                self.assertEqual(status, 409)
                self.assertEqual(conflict["error"]["code"], "review_conflict")
                assert_workspace_safe(
                    self,
                    conflict,
                    private_values=(str(root), str(source), str(project.project_dir), backend["event_id"]),
                )

                status, refreshed = self.request(
                    server,
                    "POST",
                    "/api/workspace/window",
                    payload={"selected_event_token": selected["event_token"]},
                    token=request_token,
                )
                self.assertEqual(status, 200)
                self.assertEqual(refreshed["selection"]["event"]["status"], "accepted")
                action = refreshed["selection"]["event"]["action_token"]
                workspace_service = session._workspace
                assert workspace_service is not None
                store = workspace_service._window_service.review_store
                with (
                    patch.object(store, "set_status", side_effect=OSError("private disk failure")),
                    patch("ms_event_studio.web_app.LOGGER.exception"),
                ):
                    status, failed = self.request(
                        server,
                        "POST",
                        "/api/review/decision",
                        payload={
                            "action_token": action,
                            "decision": "pending",
                            "note": "失败注入",
                        },
                        token=request_token,
                    )
                self.assertEqual(status, 500)
                self.assertEqual(failed["error"]["code"], "operation_failed")
                assert_workspace_safe(
                    self,
                    failed,
                    private_values=(
                        "private disk failure",
                        str(root),
                        str(source),
                        str(project.project_dir),
                    ),
                )
                status, unchanged = self.request(server, "GET", "/api/workspace")
                self.assertEqual(status, 200)
                selected_after = next(
                    row
                    for row in unchanged["events"]
                    if row["event_token"] == selected["event_token"]
                )
                self.assertEqual(selected_after["status"], "accepted")
                with ProjectWindowService.open(project.project_dir) as verify:
                    reasons = [row["reason"] for row in verify.review_store.audit_events()]
                self.assertEqual(reasons, ["HTTP 并发提交"])
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
