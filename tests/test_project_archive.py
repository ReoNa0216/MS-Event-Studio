import contextlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ms_event_studio.project_archive import share_project


class ProjectArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "中文项目"
        self.root.mkdir()
        self.db = self.root / "review.sqlite"
        self.writer = sqlite3.connect(self.db)
        self.addCleanup(self.writer.close)
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("CREATE TABLE labels (event_id TEXT, label TEXT)")
        self.writer.execute("INSERT INTO labels VALUES ('peak-107.079', 'accepted')")
        self.writer.commit()
        (self.root / "manifest.json").write_text('{"version": 4}', encoding="utf-8")

    def export(self):
        return share_project(self.root, self.parent, database=self.db)

    def assert_no_output(self):
        self.assertEqual(list(self.parent.glob("*.zip")), [])
        self.assertEqual(list(self.parent.glob(".studio-share-*")), [])

    def test_wal_metadata_and_immutable_history_round_trip(self):
        (self.root / "__MACOSX").mkdir()
        (self.root / "__MACOSX/._review.sqlite").write_bytes(b"metadata")
        (self.root / "._manifest.json").write_bytes(b"metadata")
        (self.root / ".DS_Store").write_bytes(b"metadata")
        (self.root / "empty").mkdir()
        history = self.root / "retired.sqlite"
        with contextlib.closing(sqlite3.connect(history)) as connection:
            connection.execute("CREATE TABLE history (id TEXT)")
        old_bytes = history.read_bytes()
        result = self.export()
        with zipfile.ZipFile(self.parent / result["filename"]) as archive:
            self.assertIsNone(archive.testzip())
            self.assertFalse(any("._" in name or "__MACOSX" in name or name.endswith(("-wal", "-shm")) for name in archive.namelist()))
            self.assertEqual(archive.read("中文项目/retired.sqlite"), old_bytes)
            archive.extractall(self.parent / "unpacked")
        with contextlib.closing(sqlite3.connect(self.parent / "unpacked/中文项目/review.sqlite")) as copied:
            self.assertEqual(copied.execute("SELECT * FROM labels").fetchall(), [("peak-107.079", "accepted")])
        self.assertEqual(self.writer.execute("SELECT * FROM labels").fetchall(), [("peak-107.079", "accepted")])
        self.assertTrue((self.root / "._manifest.json").exists())

    def test_failed_write_does_not_publish_or_leave_staging(self):
        with patch.object(zipfile.ZipFile, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.export()
        self.assert_no_output()

    def test_concurrent_review_edit_is_detected(self):
        original = zipfile.ZipFile.write
        def write(archive, *args, **kwargs):
            self.writer.execute("UPDATE labels SET label='rejected'")
            self.writer.commit()
            return original(archive, *args, **kwargs)
        with patch.object(zipfile.ZipFile, "write", write):
            with self.assertRaisesRegex(ValueError, "审阅结果发生变化"):
                self.export()
        self.assert_no_output()

    def test_concurrent_manifest_edit_is_detected(self):
        original = zipfile.ZipFile.write
        def write(archive, *args, **kwargs):
            (self.root / "manifest.json").write_text("changed generation", encoding="utf-8")
            return original(archive, *args, **kwargs)
        with patch.object(zipfile.ZipFile, "write", write):
            with self.assertRaisesRegex(ValueError, "内容发生变化"):
                self.export()
        self.assert_no_output()

    def test_destination_inside_project_is_refused(self):
        with self.assertRaisesRegex(ValueError, "以外"):
            share_project(self.root, self.root, database=self.db)
        self.assert_no_output()

    def test_existing_zip_is_not_overwritten(self):
        first = self.export()
        original = (self.parent / first["filename"]).read_bytes()
        second = self.export()
        self.assertNotEqual(first["filename"], second["filename"])
        self.assertEqual((self.parent / first["filename"]).read_bytes(), original)

    def test_unreadable_directory_fails_closed(self):
        def denied(root, **kwargs):
            kwargs["onerror"](PermissionError("unreadable project directory"))
            return iter(())
        with patch("ms_event_studio.project_archive.os.walk", side_effect=denied):
            with self.assertRaises(PermissionError):
                self.export()
        self.assert_no_output()


class BrowserProjectShareTest(unittest.TestCase):
    def test_session_share_preserves_project_and_reopens(self):
        from test_web_review_api_contract import create_guided_project
        from test_web_api_contract import wait_for_job
        from ms_event_studio.web_app import WebSession
        from ms_event_studio.project import open_project
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, project = create_guided_project(root, add_manual=True)
            with WebSession(root / "recent.json") as session:
                selected = session.register_path("project_open", project.project_dir)
                session.open_project(selected["selection_token"])
                database = session._workspace._window_service.review_store.path
                with contextlib.closing(sqlite3.connect(database)) as reader:
                    before = list(reader.iterdump())
                target = session.register_path("project_share_parent", root)
                job = session.start_project_share({"target_token": target["selection_token"]})
                result = wait_for_job(session, job["job"]["job_id"])
                self.assertEqual(result["state"], "succeeded", result)
                archive_path = root / result["result"]["export"]["display_name"]
                with zipfile.ZipFile(archive_path) as archive:
                    archive.extractall(root / "unpacked")
                copied = root / "unpacked" / project.project_dir.name
                self.assertEqual(open_project(copied).manifest, project.manifest)
                with contextlib.closing(sqlite3.connect(copied / database.relative_to(project.project_dir))) as reader:
                    self.assertEqual(list(reader.iterdump()), before)
