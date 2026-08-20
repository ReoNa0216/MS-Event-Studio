from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from ms_event_studio.errors import ProjectValidationError
from ms_event_studio.project import open_project
from ms_event_studio.web_app import WebBoundaryError, create_http_server
from ms_event_studio.window_service import ProjectWindowService
from test_web_review_api_contract import (
    assert_workspace_safe,
    create_guided_project,
    open_session,
)


ACTIVE_JOB_STATES = {"queued", "running", "cancelling"}


def wait_job(session, response: dict, *, timeout: float = 15.0) -> dict:
    identity = response["job"]["job_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = session.job(identity)["job"]
        if job["state"] not in ACTIVE_JOB_STATES:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job did not finish: {session.job(identity)}")


def fingerprint(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


class RangeChangeWebContractTest(unittest.TestCase):
    def test_preview_job_cancel_and_ready_preview_cancel_are_zero_write(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root)
            manifest_path = project.project_dir / "ms_event_project.json"
            manifest_before = manifest_path.read_bytes()
            session = open_session(root, project)
            entered = threading.Event()
            release = threading.Event()
            from ms_event_studio.range_change import preview_range_change as real_preview

            def slow_preview(*args, **kwargs):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release preview")
                return real_preview(*args, **kwargs)

            try:
                with patch(
                    "ms_event_studio.range_change.preview_range_change",
                    side_effect=slow_preview,
                ):
                    started = session.start_range_preview({"start_min": "0.75", "end_min": "2"})
                    self.assertTrue(entered.wait(5))
                    self.assertTrue(session.busy)
                    cancelled = session.cancel_job(started["job"]["job_id"])
                    self.assertTrue(cancelled["ok"])
                    release.set()
                    finished = wait_job(session, started)
                self.assertEqual(finished["state"], "cancelled")
                self.assertEqual(manifest_path.read_bytes(), manifest_before)

                ready = wait_job(
                    session,
                    session.start_range_preview({"start_min": 0.75, "end_min": 2}),
                )
                self.assertEqual(ready["state"], "succeeded")
                preview = ready["result"]["range_preview"]
                self.assertEqual(
                    session.cancel_range_preview({"preview_token": preview["preview_token"]}),
                    {"ok": True, "cancelled": True},
                )
                with self.assertRaises(WebBoundaryError) as replay:
                    session.cancel_range_preview({"preview_token": preview["preview_token"]})
                self.assertEqual(replay.exception.code, "stale_range_preview")
                self.assertEqual(manifest_path.read_bytes(), manifest_before)
                with ProjectWindowService.open(project.project_dir) as service:
                    self.assertEqual(service.review_store.audit_events(), [])
            finally:
                release.set()
                session.close()

    def test_confirmed_apply_is_single_use_atomic_and_replaces_workspace(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source, project = create_guided_project(root, add_manual=True)
            source_before = fingerprint(source)
            session = open_session(root, project)
            try:
                preview_job = wait_job(
                    session,
                    session.start_range_preview({"start_min": "0.75", "end_min": "2"}),
                )
                preview = preview_job["result"]["range_preview"]
                self.assertEqual(preview["old_range"], {"start_min": "0", "end_min": "2"})
                self.assertEqual(preview["new_range"], {"start_min": "0.75", "end_min": "2"})
                self.assertEqual(preview["impacts"]["retained_manual_count"], 1)
                assert_workspace_safe(
                    self,
                    preview,
                    private_values=(str(root), str(source), str(project.project_dir)),
                )

                with self.assertRaises(WebBoundaryError) as unconfirmed:
                    session.start_range_apply(
                        {"preview_token": preview["preview_token"], "confirmed": False}
                    )
                self.assertEqual(unconfirmed.exception.code, "confirmation_required")
                applied = wait_job(
                    session,
                    session.start_range_apply(
                        {
                            "preview_token": preview["preview_token"],
                            "confirmed": True,
                            "note": "收窄到关注区间",
                        }
                    ),
                )
                self.assertEqual(applied["state"], "succeeded")
                self.assertFalse(applied["cancellable"])
                workspace = applied["result"]["workspace"]
                self.assertEqual(
                    workspace["project"]["analysis_range"],
                    {"start_min": "0.75", "end_min": "2"},
                )
                self.assertEqual(
                    session.workspace()["project"]["analysis_range"],
                    {"start_min": "0.75", "end_min": "2"},
                )
                with self.assertRaises(WebBoundaryError) as replay:
                    session.start_range_apply(
                        {"preview_token": preview["preview_token"], "confirmed": True}
                    )
                self.assertEqual(replay.exception.code, "stale_range_preview")
                assert_workspace_safe(
                    self,
                    applied["result"],
                    private_values=(str(root), str(source), str(project.project_dir)),
                )
            finally:
                session.close()

            reopened = open_project(project.project_dir)
            self.assertEqual(reopened.manifest["analysis_range"]["start_ns"], 45_000_000_000)
            with ProjectWindowService.open(project.project_dir) as service:
                actions = [row["action"] for row in service.review_store.audit_events()]
            self.assertEqual(actions[-1], "recalculate_analysis_range")
            self.assertEqual(fingerprint(source), source_before)

    def test_stale_or_failed_apply_does_not_replace_current_project(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root)
            manifest = project.project_dir / "ms_event_project.json"
            session = open_session(root, project)
            try:
                ready = wait_job(
                    session,
                    session.start_range_preview({"start_min": "0.75", "end_min": "2"}),
                )
                token = ready["result"]["range_preview"]["preview_token"]
                before = manifest.read_bytes()
                with ProjectWindowService.open(project.project_dir) as competing:
                    row = competing.all_events()[0]
                    competing.review_store.set_status(
                        row["event_id"],
                        "pending",
                        expected_revision=row["revision"],
                        actor="other-window",
                        session_id="other-window",
                    )
                stale = wait_job(
                    session,
                    session.start_range_apply(
                        {"preview_token": token, "confirmed": True, "note": "旧预览"}
                    ),
                )
                self.assertEqual(stale["state"], "failed")
                self.assertEqual(stale["error"]["code"], "stale_range_preview")
                self.assertEqual(manifest.read_bytes(), before)
                self.assertEqual(
                    session.workspace()["project"]["analysis_range"],
                    {"start_min": "0", "end_min": "2"},
                )

                ready = wait_job(
                    session,
                    session.start_range_preview({"start_min": "0.75", "end_min": "2"}),
                )
                token = ready["result"]["range_preview"]["preview_token"]
                with patch(
                    "ms_event_studio.range_change.apply_range_change",
                    side_effect=ProjectValidationError("injected safe failure"),
                ):
                    failed = wait_job(
                        session,
                        session.start_range_apply(
                            {"preview_token": token, "confirmed": True, "note": "失败注入"}
                        ),
                    )
                self.assertEqual(failed["state"], "failed")
                self.assertEqual(failed["error"]["code"], "project_invalid")
                self.assertEqual(manifest.read_bytes(), before)
                self.assertEqual(
                    session.workspace()["project"]["analysis_range"],
                    {"start_min": "0", "end_min": "2"},
                )
            finally:
                session.close()


class ExportWebContractTest(unittest.TestCase):
    def test_review_export_defaults_accepted_pending_is_explicit_and_stale_is_excluded(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source, project = create_guided_project(root)
            source_before = fingerprint(source)
            session = open_session(root, project)
            try:
                first = session.workspace()["selection"]["event"]
                accepted = session.review_decision(
                    {"action_token": first["action_token"], "decision": "keep", "note": "保留"}
                )["workspace"]
                second = accepted["selection"]["event"]
                session.review_decision(
                    {"action_token": second["action_token"], "decision": "pending", "note": "待复核"}
                )
                preview = wait_job(
                    session,
                    session.start_range_preview({"start_min": "0.75", "end_min": "2"}),
                )["result"]["range_preview"]
                changed = wait_job(
                    session,
                    session.start_range_apply(
                        {"preview_token": preview["preview_token"], "confirmed": True}
                    ),
                )
                self.assertEqual(changed["state"], "succeeded")

                accepted_path = root / "accepted.csv"
                accepted_target = session.register_path("review_export_file", accepted_path)
                default_job = wait_job(
                    session,
                    session.start_review_export(
                        {"target_token": accepted_target["selection_token"]}
                    ),
                )
                self.assertEqual(default_job["state"], "succeeded")
                self.assertEqual(default_job["result"]["export"]["row_count"], 0)
                self.assertNotIn(str(root), json.dumps(default_job, ensure_ascii=False))
                with self.assertRaises(WebBoundaryError) as replay:
                    session.start_review_export(
                        {"target_token": accepted_target["selection_token"]}
                    )
                self.assertEqual(replay.exception.code, "stale_selection")

                pending_path = root / "accepted-and-pending.csv"
                pending_target = session.register_path("review_export_file", pending_path)
                pending_job = wait_job(
                    session,
                    session.start_review_export(
                        {
                            "target_token": pending_target["selection_token"],
                            "include_pending": True,
                            "note": "包含待定",
                        }
                    ),
                )
                self.assertEqual(pending_job["result"]["export"]["row_count"], 1)
                text = pending_path.read_text(encoding="utf-8-sig")
                self.assertIn("pending", text)
                self.assertNotIn("accepted", text)
                self.assertEqual(fingerprint(source), source_before)
            finally:
                session.close()

    def test_audit_export_creates_package_inside_selected_folder_and_cleans_failure(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root)
            session = open_session(root, project)
            try:
                parent = root / "exports"
                parent.mkdir()
                keep = parent / "keep.txt"
                keep.write_text("keep", encoding="utf-8")
                selection = session.register_path("audit_export_parent", parent)
                with patch("pandas.DataFrame.to_parquet", side_effect=OSError("injected")):
                    failed = wait_job(
                        session,
                        session.start_audit_export(
                            {"target_token": selection["selection_token"], "note": "失败清理"}
                        ),
                    )
                self.assertEqual(failed["state"], "failed")
                self.assertEqual(list(parent.iterdir()), [keep])
                self.assertEqual(list(parent.glob(".*.machine-exporting-*")), [])
                with self.assertRaises(WebBoundaryError) as replay:
                    session.start_audit_export(
                        {"target_token": selection["selection_token"]}
                    )
                self.assertEqual(replay.exception.code, "stale_selection")

                selection = session.register_path("audit_export_parent", parent)
                succeeded = wait_job(
                    session,
                    session.start_audit_export(
                        {"target_token": selection["selection_token"], "note": "完整导出"}
                    ),
                )
                self.assertEqual(succeeded["state"], "succeeded")
                self.assertEqual(succeeded["result"]["export"]["row_count"], 3)
                packages = [path for path in parent.iterdir() if path.is_dir()]
                self.assertEqual(len(packages), 1)
                successful_target = packages[0]
                self.assertEqual(
                    succeeded["result"]["export"]["display_name"],
                    successful_target.name,
                )
                self.assertEqual(
                    sorted(path.name for path in successful_target.iterdir()),
                    ["checksums.sha256", "events.parquet", "manifest.json"],
                )
                assert_workspace_safe(
                    self,
                    succeeded["result"],
                    private_values=(str(root), str(project.project_dir)),
                )
            finally:
                session.close()


class RangeExportHTTPContractTest(unittest.TestCase):
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

    def test_http_routes_use_write_token_closed_fields_and_safe_job_results(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source, project = create_guided_project(root)
            session = open_session(root, project)
            server = create_http_server(session=session)
            try:
                server.start()
                status, bootstrap = self.request(server, "GET", "/api/bootstrap")
                self.assertEqual(status, 200)
                token = bootstrap["request_token"]
                status, error = self.request(
                    server,
                    "POST",
                    "/api/range-changes/preview",
                    payload={"start_min": "0.75", "end_min": "2"},
                )
                self.assertEqual(status, 403)
                self.assertEqual(error["error"]["code"], "invalid_request_token")
                status, error = self.request(
                    server,
                    "POST",
                    "/api/range-changes/preview",
                    payload={"start_min": "0.75", "end_min": "2", "raw": "private"},
                    token=token,
                )
                self.assertEqual(status, 400)
                status, started = self.request(
                    server,
                    "POST",
                    "/api/range-changes/preview",
                    payload={"start_min": "0.75", "end_min": "2"},
                    token=token,
                )
                self.assertEqual(status, 202)
                job = wait_job(session, started)
                self.assertEqual(job["state"], "succeeded")
                assert_workspace_safe(
                    self,
                    job,
                    private_values=(str(root), str(source), str(project.project_dir)),
                )
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
