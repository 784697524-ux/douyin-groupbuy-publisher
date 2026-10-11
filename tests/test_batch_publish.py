from __future__ import annotations

import asyncio
import contextlib
import csv
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_douyin_groupbuy_batch.py"
SPEC = importlib.util.spec_from_file_location("batch_publish", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "video.mp4").write_bytes(b"synthetic video")
        (self.root / "cover.png").write_bytes(b"synthetic cover")
        self.manifest = self.root / "manifest.json"
        self.item = {"id": "demo", "title": "测试作品", "desc": "测试正文", "file": "video.mp4",
                     "cover": "cover.png", "location": "测试商场", "tags": ["测试"]}
        self.write_items([self.item])

    def write_items(self, items):
        self.manifest.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")

    def jobs(self):
        return MODULE.load_manifest(self.manifest, "test_profile", "expected_demo")

    def test_relative_assets_and_dry_run_do_not_import_live_dependencies(self):
        job = self.jobs()[0]
        self.assertEqual(job["file"], str((self.root / "video.mp4").resolve()))
        with patch.object(MODULE, "live", side_effect=AssertionError("Live path forbidden")), contextlib.redirect_stdout(io.StringIO()) as output:
            result = MODULE.main(["--manifest", str(self.manifest), "--state-dir", str(self.root / "states"),
                                  "--account", "test_profile", "--expected-account", "expected_demo", "--dry-run"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["pending"], 1)
        self.assertFalse((self.root / "states").exists())

    def test_all_items_are_validated_before_live(self):
        second = {**self.item, "id": "invalid", "file": "missing.mp4"}
        self.write_items([self.item, second])
        with patch.object(MODULE, "live") as live, contextlib.redirect_stderr(io.StringIO()):
            result = MODULE.main(["--manifest", str(self.manifest), "--state-dir", str(self.root / "states"),
                                  "--account", "test_profile", "--expected-account", "expected_demo"])
        self.assertEqual(result, 1)
        live.assert_not_called()

    def test_blank_description_long_title_and_wrong_asset_suffix_are_rejected(self):
        for changed in ({"desc": "   "}, {"title": "长" * 31}, {"cover": "video.mp4"}):
            self.write_items([{**self.item, **changed}])
            with self.assertRaises(ValueError):
                self.jobs()

    def test_source_hash_and_changed_cover_guard_resume(self):
        self.item["source_sha256"] = "0" * 64
        self.write_items([self.item])
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            self.jobs()
        self.item["source_sha256"] = MODULE.sha256(self.root / "video.mp4")
        self.write_items([self.item])
        job = self.jobs()[0]
        state_dir = self.root / "states"
        MODULE.save(state_dir / "demo" / "state.json", {"stage": "prepared", "fingerprint": job["fingerprint"]})
        (self.root / "cover.png").write_bytes(b"changed cover")
        with self.assertRaisesRegex(RuntimeError, "fingerprint"):
            MODULE.pending_jobs(self.jobs(), state_dir)

    def test_resume_skips_matching_submitted_and_stops_uncertain(self):
        job = self.jobs()[0]
        state_dir = self.root / "states"
        path = state_dir / "demo" / "state.json"
        MODULE.save(path, {"stage": "submitted", "fingerprint": job["fingerprint"],
                           "receipt": {"matched": True, "aweme_id": "synthetic_receipt"}})
        self.assertEqual(MODULE.pending_jobs([job], state_dir), [])
        for stage in ("submission_uncertain", "submitted_unverified", "prepare_failed"):
            MODULE.save(path, {"stage": stage, "fingerprint": job["fingerprint"]})
            with self.assertRaises(RuntimeError):
                MODULE.pending_jobs([job], state_dir)


class ResultTests(unittest.TestCase):
    setUp = ManifestTests.setUp
    write_items = ManifestTests.write_items
    jobs = ManifestTests.jobs

    def submitted(self, job, **changes):
        state = {"stage": "submitted", "fingerprint": job["fingerprint"],
                 "receipt": {"matched": True, "aweme_id": "1234567890123456789", "account": "expected_demo",
                             "title": job["title"], "create_time": 100, "status": {"in_reviewing": True}}}
        state.update(changes)
        MODULE.save(self.root / "states" / job["id"] / "state.json", state)

    def test_collect_old_receipt_derives_link_and_exports_without_live_or_state_changes(self):
        job = self.jobs()[0]
        self.submitted(job)
        state_path = self.root / "states/demo/state.json"
        before = state_path.read_bytes()
        with patch.object(MODULE, "live", side_effect=AssertionError("No browser or publishing")), contextlib.redirect_stdout(io.StringIO()):
            result = MODULE.main(["--manifest", str(self.manifest), "--state-dir", str(self.root / "states"),
                                  "--account", "test_profile", "--expected-account", "expected_demo", "--collect-results"])
        self.assertEqual(result, 0)
        self.assertEqual(state_path.read_bytes(), before)
        report = MODULE.read(self.root / "states/publish-results.json")
        self.assertEqual(report["verified_receipts"], 1)
        self.assertFalse(report["current_platform_status_checked"])
        self.assertEqual(report["items"][0]["public_url"], "https://www.douyin.com/video/1234567890123456789")
        with (self.root / "states/publish-results.csv").open(encoding="utf-8-sig", newline="") as source:
            self.assertEqual(list(csv.DictReader(source))[0]["title"], job["title"])
        self.assertIn(report["items"][0]["public_url"], (self.root / "states/publish-results.md").read_text())

    def test_partial_batch_preserves_success_and_never_links_uncertain_wrong_account_or_changed_content(self):
        self.write_items([{**self.item, "id": name} for name in ("ok", "uncertain", "wrong", "changed", "missing")])
        jobs = self.jobs()
        self.submitted(jobs[0])
        self.submitted(jobs[1], stage="submitted_unverified")
        self.submitted(jobs[2], receipt={"matched": True, "aweme_id": "123", "account": "wrong_demo", "title": self.item["title"]})
        self.submitted(jobs[3], fingerprint="another-content")
        summary = MODULE.export_results(jobs, self.root / "states", "expected_demo")
        self.assertEqual(summary["verified_receipts"], 1)
        rows = MODULE.read(self.root / "states/publish-results.json")["items"]
        self.assertTrue(rows[0]["public_url"])
        self.assertTrue(all(not row["public_url"] for row in rows[1:]))

    def test_resume_exports_all_links_without_republish(self):
        self.submitted(self.jobs()[0])
        with patch.object(MODULE, "live", side_effect=AssertionError("Already submitted")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(MODULE.main(["--manifest", str(self.manifest), "--state-dir", str(self.root / "states"),
                                         "--account", "test_profile", "--expected-account", "expected_demo"]), 0)
        self.assertEqual(MODULE.read(self.root / "states/publish-results.json")["verified_receipts"], 1)

    def test_live_failure_still_exports_earlier_success_without_retry(self):
        self.write_items([{**self.item, "id": name} for name in ("ok", "failed")])

        async def fail_after_first(args, jobs):
            self.submitted(jobs[0])
            self.submitted(jobs[1], stage="submitted_unverified")
            raise RuntimeError("Synthetic interruption")

        with patch.object(MODULE, "live", new=AsyncMock(side_effect=fail_after_first)) as live, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = MODULE.main(["--manifest", str(self.manifest), "--state-dir", str(self.root / "states"),
                                  "--account", "test_profile", "--expected-account", "expected_demo"])
        self.assertEqual(result, 1)
        live.assert_awaited_once()
        self.assertEqual(MODULE.read(self.root / "states/publish-results.json")["verified_receipts"], 1)
        self.assertEqual(MODULE.read(self.root / "states/failed/state.json")["stage"], "submitted_unverified")

    def test_invalid_identifiers_never_become_public_links(self):
        for identifier in (None, "", "../other", "https://example.com", "123?redirect=bad"):
            self.assertEqual(MODULE.public_video_url(identifier), "")


class AccountTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_loaded_real_id_before_exact_comparison(self):
        class Page:
            loaded = False

            async def goto(self, url, **kwargs):
                self.url = url

            async def wait_for_function(self, function, **kwargs):
                self.loaded = True

            def locator(self, selector):
                return self

            async def inner_text(self):
                if not self.loaded:
                    raise AssertionError("Account was read before the real ID loaded")
                return "测试昵称\n抖音号：\nexpected_demo\n管理作品"

        page = Page()
        await MODULE.verify_account(page, "expected_demo")
        with self.assertRaisesRegex(RuntimeError, "expected-account"):
            await MODULE.verify_account(Page(), "wrong_demo")


class PublishTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.job = {"title": "测试作品", "desc": "测试正文", "location": "测试商场", "fingerprint": "same-content",
                    "exclude_aweme_ids": ["excluded"]}
        self.prepared = {"stage": "prepared", "fingerprint": "same-content", "declaration": {"verified": True, "selected": "AI生成"},
                         "cover": {"verified": True}, "location": {"verified": True}}
        MODULE.save(self.path, self.prepared)
        path, job = self.path, self.job

        class Page:
            first = property(lambda self: self)
            clicks = 0
            fail_navigation = False

            def locator(self, selector):
                return self

            async def input_value(self):
                return job["title"][:30]

            def get_by_text(self, text, **kwargs):
                return self

            def get_by_role(self, role, **kwargs):
                return self

            async def wait_for(self, **kwargs):
                pass

            async def click(self, **kwargs):
                state = MODULE.read(path)
                if state["stage"] != "submission_uncertain" or not state.get("submitted_after"):
                    raise AssertionError("Click occurred before atomic uncertainty state")
                self.clicks += 1

            async def wait_for_url(self, pattern, **kwargs):
                if self.fail_navigation:
                    raise TimeoutError("Synthetic post-click navigation timeout")

        self.page = Page()

    async def test_timeout_after_click_does_not_allow_second_click(self):
        self.page.fail_navigation = True
        with patch.object(MODULE, "verify_location", new=AsyncMock(return_value={"verified": True})), \
                patch.object(MODULE, "work_list", new=AsyncMock(return_value=[{"aweme_id": "old", "author": {"unique_id": "expected_demo"}}])):
            with self.assertRaises(TimeoutError):
                await MODULE.publish(object(), self.page, self.job, "expected_demo", self.path)
            with self.assertRaises(RuntimeError):
                await MODULE.publish(object(), self.page, self.job, "expected_demo", self.path)
        self.assertEqual(self.page.clicks, 1)
        state = MODULE.read(self.path)
        self.assertEqual(state["stage"], "submitted_unverified")
        self.assertEqual(state["exclude_aweme_ids"], ["excluded", "old"])

    async def test_atomic_uncertain_write_failure_means_zero_clicks(self):
        original = MODULE.save

        def save(path, state):
            if state["stage"] == "submission_uncertain":
                raise OSError("Synthetic disk failure")
            original(path, state)

        with patch.object(MODULE, "save", side_effect=save), \
                patch.object(MODULE, "verify_location", new=AsyncMock(return_value={"verified": True})), \
                patch.object(MODULE, "work_list", new=AsyncMock(return_value=[])):
            with self.assertRaises(OSError):
                await MODULE.publish(object(), self.page, self.job, "expected_demo", self.path)
        self.assertEqual(self.page.clicks, 0)

    async def test_failed_baseline_is_prepare_failed_without_click(self):
        with patch.object(MODULE, "verify_location", new=AsyncMock(return_value={"verified": True})), \
                patch.object(MODULE, "work_list", new=AsyncMock(side_effect=RuntimeError("Synthetic HTTP failure"))):
            with self.assertRaises(RuntimeError):
                await MODULE.publish(object(), self.page, self.job, "expected_demo", self.path)
        self.assertEqual(self.page.clicks, 0)
        self.assertEqual(MODULE.read(self.path)["stage"], "prepare_failed")

    async def test_wrong_baseline_account_stops_before_click(self):
        baseline = [{"aweme_id": "old", "author": {"unique_id": "wrong_demo"}}]
        with patch.object(MODULE, "verify_location", new=AsyncMock(return_value={"verified": True})), \
                patch.object(MODULE, "work_list", new=AsyncMock(return_value=baseline)):
            with self.assertRaisesRegex(RuntimeError, "账号"):
                await MODULE.publish(object(), self.page, self.job, "expected_demo", self.path)
        self.assertEqual(self.page.clicks, 0)

    async def test_strict_receipt_is_required_after_unique_click(self):
        receipt = {"matched": True, "aweme_id": "new", "account": "expected_demo"}
        with patch.object(MODULE, "verify_location", new=AsyncMock(return_value={"verified": True})), \
                patch.object(MODULE, "work_list", new=AsyncMock(return_value=[])), \
                patch.object(MODULE, "receipt", new=AsyncMock(return_value=receipt)):
            await MODULE.publish(object(), self.page, self.job, "expected_demo", self.path)
        self.assertEqual(self.page.clicks, 1)
        self.assertEqual(MODULE.read(self.path)["stage"], "submitted")


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.job = {"title": "测试作品", "desc": "测试正文"}
        self.item = {"aweme_id": "new", "item_title": "测试作品", "caption": "测试正文 #测试", "create_time": 100,
                     "author": {"unique_id": "expected_demo"}}

    def test_old_title_time_and_wrong_author_cannot_be_receipts(self):
        self.assertIsNone(MODULE.match_receipt([self.item], self.job, "expected_demo", {"new"}, 90))
        self.assertIsNone(MODULE.match_receipt([self.item], self.job, "expected_demo", set(), 101))
        self.assertIsNone(MODULE.match_receipt([{**self.item, "caption": "旧正文"}], self.job, "expected_demo", set(), 90))
        with self.assertRaisesRegex(RuntimeError, "账号"):
            MODULE.match_receipt([self.item], self.job, "wrong_demo", set(), 90)

    def test_matching_new_id_and_multiple_candidates(self):
        receipt = MODULE.match_receipt([self.item], self.job, "expected_demo", set(), 90)
        self.assertEqual(receipt["aweme_id"], "new")
        with self.assertRaisesRegex(RuntimeError, "多个"):
            MODULE.match_receipt([self.item, {**self.item, "aweme_id": "another_new"}], self.job, "expected_demo", set(), 90)


class LocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_selected_poi_rejects_service_desk_suffix(self):
        class Label:
            first = property(lambda self: self)

            def __init__(self, text):
                self.text = text

            async def count(self):
                return 1

            async def inner_text(self):
                return self.text

            async def is_visible(self):
                return True

            def locator(self, css):
                return self

        class Selects:
            def __init__(self, location):
                self.items = [Label(text) for text in ("位置", "带货模式", location)]

            async def count(self):
                return len(self.items)

            def nth(self, index):
                return self.items[index]

        class Page:
            def __init__(self, location):
                self.selects = Selects(location)

            def locator(self, selector):
                return self.selects

        result = await MODULE.verify_location(Page("测试商场"), "测试商场")
        self.assertTrue(result["verified"])
        with self.assertRaises(RuntimeError):
            await MODULE.verify_location(Page("测试商场服务台"), "测试商场")


class PrepareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        video, cover = root / "video.mp4", root / "cover.png"
        video.write_bytes(b"synthetic video")
        cover.write_bytes(b"synthetic cover")
        self.job = {"title": "测试作品", "desc": "测试正文", "tags": [], "location": "测试商场", "fingerprint": "content",
                    "file": str(video), "cover": str(cover), "file_sha256": MODULE.sha256(video), "cover_sha256": MODULE.sha256(cover)}
        self.path = root / "states" / "state.json"
        self.events = []
        events, job, path = self.events, self.job, self.path

        class Page:
            first = property(lambda self: self)

            def locator(self, selector):
                return self

            async def goto(self, url, **kwargs):
                pass

            async def set_input_files(self, file, **kwargs):
                events.append("upload")

            async def wait_for_url(self, url, **kwargs):
                pass

            async def wait_for(self, **kwargs):
                pass

            async def input_value(self):
                return job["title"]

            async def screenshot(self, **kwargs):
                events.append("screenshot")

        class Context:
            async def new_page(self):
                events.append("new_page")
                return Page()

        class OldPage:
            def is_closed(self):
                return False

            async def close(self):
                if MODULE.read(path).get("stage") != "prepared":
                    raise AssertionError("Old page closed before preparation state was saved")
                events.append("close_old")

        self.context, self.old = Context(), OldPage()
        self.app = type("App", (), {})()
        self.app.validate_upload_args = AsyncMock()
        self.app.ensure_logged_in_for_upload = AsyncMock(side_effect=lambda context, page: page)
        self.app.fill_title_and_description = AsyncMock()
        self.app.set_location = AsyncMock()

    async def test_changed_source_after_manifest_validation_never_uploads_or_closes_old_page(self):
        Path(self.job["file"]).write_bytes(b"changed after validation")
        with self.assertRaisesRegex(RuntimeError, "hash"):
            await MODULE.prepare(self.context, self.old, self.job, self.app, "expected_demo", self.path)
        self.assertEqual(self.events, ["new_page"])
        self.app.validate_upload_args.assert_not_called()

    async def test_new_page_prepares_and_saves_before_old_page_closes(self):
        verified = {"verified": True}
        with patch.object(MODULE, "verify_account", new=AsyncMock()), \
                patch.object(MODULE, "verify_location", new=AsyncMock(return_value=verified)), \
                patch.object(MODULE, "set_cover", new=AsyncMock(return_value=verified)), \
                patch.object(MODULE, "ai_declaration", new=AsyncMock(return_value={"verified": True, "selected": "AI生成"})):
            await MODULE.prepare(self.context, self.old, self.job, self.app, "expected_demo", self.path)
        self.assertEqual(self.events, ["new_page", "upload", "screenshot", "close_old"])


if __name__ == "__main__":
    unittest.main()
