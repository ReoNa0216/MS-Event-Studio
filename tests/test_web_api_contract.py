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

import numpy as np

from _fixtures import spectrum_lines, write_ms_file
from ms_event_studio.errors import CancelledError
from ms_event_studio.web_app import WebBoundaryError, WebSession, create_http_server
from ms_event_studio.web_models import AnalysisRangeView, PathRole, SourceInspectionView


def make_source(path: Path) -> Path:
    signal = np.zeros(1201)
    signal[[300, 600, 900]] = [1000.0, 1500.0, 1200.0]
    return write_ms_file(
        path,
        [
            spectrum_lines(
                index,
                index + 1,
                f"{index / 600.0:.12f}",
                intensities=[0.0, float(signal[index]), 10.0, 0.0],
            )
            for index in range(len(signal))
        ],
    )


def wait_for_job(session: WebSession, identity: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = session.job(identity)["job"]
        if job["state"] in {"succeeded", "cancelled", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {identity} did not finish")


def recursive_keys(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            result.append(str(key))
            result.extend(recursive_keys(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(recursive_keys(child))
    return result


def assert_browser_safe(test: unittest.TestCase, payload: object, *private_values: str) -> None:
    forbidden_keys = {
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
    }
    keys = {key.casefold() for key in recursive_keys(payload)}
    test.assertTrue(keys.isdisjoint(forbidden_keys), keys.intersection(forbidden_keys))
    serialized = json.dumps(payload, ensure_ascii=False)
    for private in private_values:
        test.assertNotIn(private, serialized)


class WebViewModelContractTest(unittest.TestCase):
    def test_models_expose_decimal_minutes_and_opaque_tokens_only(self):
        view = SourceInspectionView(
            inspection_token="opaque-inspection-token",
            source_name="sample.txt",
            available_range=AnalysisRangeView.from_nanoseconds(30_000_000_000, 90_000_000_000),
            scan_count=12,
            size_bytes=345,
        ).to_dict()
        self.assertEqual(view["available_range"], {"start_min": "0.5", "end_min": "1.5"})
        self.assertEqual(view["display_name"], "sample.txt")
        assert_browser_safe(self, view)

    def test_path_roles_are_strict(self):
        self.assertIs(PathRole.parse("source_file"), PathRole.SOURCE_FILE)
        with self.assertRaises(ValueError):
            PathRole.parse("source")
        with self.assertRaises(ValueError):
            PathRole.parse(1)


class WebSessionContractTest(unittest.TestCase):
    def test_inspect_and_create_keep_source_read_only_and_paths_private(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = make_source(root / "private-source.txt")
            target = root / "private-project"
            before = (source.stat().st_size, source.stat().st_mtime_ns, hashlib.sha256(source.read_bytes()).hexdigest())
            session = WebSession(root / "recent.json")
            try:
                source_selection = session.register_path("source_file", source)
                target_selection = session.register_path("project_target", target)
                assert_browser_safe(self, source_selection, str(source), str(root))
                assert_browser_safe(self, target_selection, str(target), str(root))

                started = session.start_source_inspection(source_selection["selection_token"])
                inspection_job = wait_for_job(session, started["job"]["job_id"])
                self.assertEqual(inspection_job["state"], "succeeded")
                inspection = inspection_job["result"]
                self.assertEqual(inspection["available_range"], {"start_min": "0", "end_min": "2"})
                self.assertEqual(inspection["scan_count"], 1201)
                assert_browser_safe(self, inspection_job, str(source), str(root))

                created = session.start_project_creation(
                    {
                        "source_token": source_selection["selection_token"],
                        "inspection_token": inspection["inspection_token"],
                        "target_token": target_selection["selection_token"],
                        "display_name": "边界测试项目",
                        "analysis_start_min": "0",
                        "analysis_end_min": "2",
                    }
                )
                creation_job = wait_for_job(session, created["job"]["job_id"])
                self.assertEqual(creation_job["state"], "succeeded", creation_job.get("error"))
                self.assertEqual(creation_job["result"]["project"]["event_count"], 3)
                self.assertTrue((target / "ms_event_project.json").is_file())
                after = (source.stat().st_size, source.stat().st_mtime_ns, hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertEqual(after, before)
                bootstrap = session.bootstrap()
                self.assertEqual(bootstrap["view"], "project")
                self.assertEqual(bootstrap["active_project"]["display_name"], "边界测试项目")
                assert_browser_safe(self, bootstrap, str(source), str(target), str(root))
            finally:
                session.close()

            reopened = WebSession(root / "recent.json")
            try:
                bootstrap = reopened.bootstrap()
                self.assertEqual(len(bootstrap["recent_projects"]), 1)
                token = bootstrap["recent_projects"][0]["project_token"]
                response = reopened.open_project(token)
                self.assertEqual(response["project"]["event_count"], 3)
                assert_browser_safe(self, response, str(target), str(root))
            finally:
                reopened.close()

    def test_source_inspection_can_be_cancelled_without_exposing_internal_error(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = root / "secret.txt"
            source.write_text("placeholder", encoding="ascii")
            started = threading.Event()

            def cancellable(_path, *, cancel_check, progress_callback):
                progress_callback(type("Progress", (), {"phase": "parsing", "bytes_read": 1, "total_bytes": 100, "parsed_spectra": 0})())
                started.set()
                while not cancel_check():
                    time.sleep(0.005)
                raise CancelledError(f"cancelled private path {_path}")

            session = WebSession(root / "recent.json", max_workers=1)
            server = create_http_server(session=session)
            try:
                token = session.register_path("source_file", source)["selection_token"]
                with patch("ms_event_studio.web_app.inspect_project_source", side_effect=cancellable):
                    job_id = session.start_source_inspection(token)["job"]["job_id"]
                    self.assertTrue(started.wait(timeout=2.0))
                    self.assertTrue(session.busy)
                    self.assertTrue(server.busy)
                    response = session.cancel_job(job_id)
                    self.assertIn(response["job"]["state"], {"cancelling", "cancelled"})
                    final = wait_for_job(session, job_id)
                self.assertEqual(final["state"], "cancelled")
                self.assertFalse(session.busy)
                self.assertFalse(server.busy)
                self.assertNotIn("error", final)
                assert_browser_safe(self, final, str(source), str(root))
            finally:
                server.stop()

    def test_tokens_cannot_cross_path_roles_and_payload_fields_are_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            source.write_text("x", encoding="ascii")
            session = WebSession(root / "recent.json")
            try:
                token = session.register_path("source_file", source)["selection_token"]
                with self.assertRaises(WebBoundaryError):
                    session.open_project(token)
                with self.assertRaises(WebBoundaryError):
                    session.start_project_creation({"raw_path": str(source)})
            finally:
                session.close()

    def test_failed_inspection_returns_a_safe_error_without_the_source_path(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = root / "confidential-source.txt"
            source.write_text("not an MS export", encoding="ascii")
            session = WebSession(root / "recent.json")
            try:
                token = session.register_path("source_file", source)["selection_token"]
                job_id = session.start_source_inspection(token)["job"]["job_id"]
                failed = wait_for_job(session, job_id)
                self.assertEqual(failed["state"], "failed")
                self.assertEqual(failed["error"]["code"], "source_invalid")
                assert_browser_safe(self, failed, str(source), str(root))
            finally:
                session.close()


class LoopbackHTTPContractTest(unittest.TestCase):
    @staticmethod
    def request(
        server,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        token: str | None = None,
        origin: str | None = None,
        host: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        parsed = urlparse(server.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        headers: dict[str, str] = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["X-MS-Event-Token"] = token
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        result = (response.status, {key.casefold(): value for key, value in response.getheaders()}, raw)
        connection.close()
        return result

    def test_random_capability_url_csp_and_same_origin_guards(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            first = create_http_server(recent_path=root / "recent-a.json")
            second = create_http_server(recent_path=root / "recent-b.json")
            try:
                self.assertNotEqual(first.server_address[1], second.server_address[1])
                self.assertNotEqual(first.capability_url, second.capability_url)
                self.assertTrue(first.base_url.startswith("http://127.0.0.1:"))
                first.start()

                status, headers, body = self.request(first, "GET", "/api/health")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {"ok": True, "service": "MS Event Studio"})
                self.assertIn("default-src 'self'", headers["content-security-policy"])
                self.assertIn("object-src 'none'", headers["content-security-policy"])

                status, headers, _ = self.request(first, "GET", "/")
                self.assertEqual(status, 200)
                self.assertNotIn("unsafe-eval", headers["content-security-policy"])
                capability_path = urlparse(first.capability_url)
                status, headers, _ = self.request(
                    first,
                    "GET",
                    capability_path.path + "?" + capability_path.query,
                )
                self.assertEqual(status, 200)
                self.assertIn("unsafe-eval", headers["content-security-policy"])

                status, _, body = self.request(
                    first,
                    "GET",
                    "/api/bootstrap",
                    origin="https://attacker.invalid",
                )
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(body)["error"]["code"], "cross_origin_blocked")
                status, _, _ = self.request(first, "GET", "/api/bootstrap", host="attacker.invalid")
                self.assertEqual(status, 403)

                status, _, body = self.request(
                    first,
                    "POST",
                    "/api/source-inspections",
                    payload={"source_token": "not-a-token"},
                )
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(body)["error"]["code"], "invalid_request_token")
            finally:
                first.stop()
                second.stop()
        with self.assertRaises(ValueError):
            create_http_server("0.0.0.0")

    def test_native_dialog_and_bootstrap_never_return_the_selected_path(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = root / "private-source.txt"
            source.write_text("not parsed in this test", encoding="ascii")

            def dialog(*, role: str, title: str):
                self.assertEqual(role, "source_file")
                self.assertTrue(title)
                return source

            server = create_http_server(
                recent_path=root / "recent.json",
                path_dialog=dialog,
            )
            try:
                server.start()
                status, _, raw = self.request(server, "GET", "/api/bootstrap")
                self.assertEqual(status, 200)
                bootstrap = json.loads(raw)
                assert_browser_safe(self, bootstrap, str(source), str(root))
                status, _, raw = self.request(
                    server,
                    "POST",
                    "/api/select-path",
                    payload={"role": "source_file"},
                    token=bootstrap["request_token"],
                )
                self.assertEqual(status, 200)
                selection = json.loads(raw)
                self.assertEqual(selection["display_name"], source.name)
                self.assertIn("selection_token", selection)
                assert_browser_safe(self, selection, str(source), str(root))
            finally:
                server.stop()

    def test_http_inspect_create_poll_and_open_round_trip(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = make_source(root / "source-round-trip.txt")
            target = root / "project-round-trip"
            selected = {
                "source_file": source,
                "project_target": target,
                "project_open": target,
            }

            def dialog(*, role: str, title: str):
                self.assertTrue(title)
                return selected[role]

            server = create_http_server(
                recent_path=root / "recent.json",
                path_dialog=dialog,
            )
            try:
                server.start()
                status, _, raw = self.request(server, "GET", "/api/bootstrap")
                self.assertEqual(status, 200)
                request_token = json.loads(raw)["request_token"]

                def select(role: str) -> dict:
                    status, _, body = self.request(
                        server,
                        "POST",
                        "/api/select-path",
                        payload={"role": role},
                        token=request_token,
                    )
                    self.assertEqual(status, 200, body)
                    return json.loads(body)

                source_selection = select("source_file")
                target_selection = select("project_target")
                status, _, raw = self.request(
                    server,
                    "POST",
                    "/api/source-inspections",
                    payload={"source_token": source_selection["selection_token"]},
                    token=request_token,
                )
                self.assertEqual(status, 202, raw)
                inspection_job_id = json.loads(raw)["job"]["job_id"]
                deadline = time.monotonic() + 15
                while True:
                    status, _, raw = self.request(
                        server,
                        "GET",
                        f"/api/jobs/{inspection_job_id}",
                    )
                    self.assertEqual(status, 200, raw)
                    inspection_job = json.loads(raw)["job"]
                    if inspection_job["state"] in {"succeeded", "cancelled", "failed"}:
                        break
                    if time.monotonic() >= deadline:
                        self.fail("HTTP source inspection did not finish")
                    time.sleep(0.01)
                self.assertEqual(inspection_job["state"], "succeeded", inspection_job.get("error"))
                inspection = inspection_job["result"]

                status, _, raw = self.request(
                    server,
                    "POST",
                    "/api/projects",
                    payload={
                        "source_token": source_selection["selection_token"],
                        "inspection_token": inspection["inspection_token"],
                        "target_token": target_selection["selection_token"],
                        "display_name": "HTTP 回环项目",
                        "analysis_start_min": inspection["available_range"]["start_min"],
                        "analysis_end_min": inspection["available_range"]["end_min"],
                    },
                    token=request_token,
                )
                self.assertEqual(status, 202, raw)
                creation_job_id = json.loads(raw)["job"]["job_id"]
                deadline = time.monotonic() + 15
                while True:
                    status, _, raw = self.request(
                        server,
                        "GET",
                        f"/api/jobs/{creation_job_id}",
                    )
                    self.assertEqual(status, 200, raw)
                    creation_job = json.loads(raw)["job"]
                    if creation_job["state"] in {"succeeded", "cancelled", "failed"}:
                        break
                    if time.monotonic() >= deadline:
                        self.fail("HTTP project creation did not finish")
                    time.sleep(0.01)
                self.assertEqual(creation_job["state"], "succeeded", creation_job.get("error"))
                self.assertEqual(creation_job["result"]["project"]["event_count"], 3)

                project_selection = select("project_open")
                status, _, raw = self.request(
                    server,
                    "POST",
                    "/api/projects/open",
                    payload={"project_token": project_selection["selection_token"]},
                    token=request_token,
                )
                self.assertEqual(status, 200, raw)
                opened = json.loads(raw)
                self.assertEqual(opened["project"]["display_name"], "HTTP 回环项目")
                assert_browser_safe(self, opened, str(source), str(target), str(root))
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
