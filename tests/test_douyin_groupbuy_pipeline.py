from __future__ import annotations

import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "douyin_groupbuy_pipeline.py"
SINGLE_BROWSER_PATCH = Path(__file__).resolve().parents[1] / "patches" / "douyin-single-browser-publish.patch"
PERSISTENT_LOGIN_PATCH = Path(__file__).resolve().parents[1] / "patches" / "douyin-persistent-login-publish.patch"
SPEC = importlib.util.spec_from_file_location("douyin_groupbuy_pipeline", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class PublishVideoTests(unittest.TestCase):
    def test_publish_video_passes_groupbuy_location_to_sau(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "video.mp4"
            video.write_bytes(b"video")
            args = argparse.Namespace(
                account="demo_douyin",
                file=video,
                title="中大银泰19.9逛吃卡",
                desc="电影、饮品、甜品都能安排。",
                tags="杭州中大银泰,杭州团购",
                location="杭州中大银泰百货",
                thumbnail=None,
                debug=True,
                headless=False,
            )

            with patch.object(MODULE, "run") as run_mock:
                MODULE.publish_video(args)

            command = run_mock.call_args.args[0]
            self.assertEqual(command[1:3], ["douyin", "upload-video"])
            self.assertEqual(command[command.index("--location") + 1], "杭州中大银泰百货")
            self.assertIn("--headed", command)
            self.assertIn("--debug", command)

    def test_publish_video_rejects_missing_file(self) -> None:
        args = argparse.Namespace(file=Path("missing.mp4"), location="杭州中大银泰百货")
        with self.assertRaisesRegex(SystemExit, "video not found"):
            MODULE.publish_video(args)


class RuntimePatchTests(unittest.TestCase):
    def test_single_browser_patch_removes_cookie_preflight(self) -> None:
        patch_text = SINGLE_BROWSER_PATCH.read_text(encoding="utf-8")
        self.assertIn("DouyinSingleBrowserPublishTests", patch_text)
        self.assertIn("ensure_douyin_upload_page", patch_text)
        self.assertIn("-    is_ready = await douyin_setup", patch_text)

    def test_persistent_login_patch_keeps_publish_session(self) -> None:
        patch_text = PERSISTENT_LOGIN_PATCH.read_text(encoding="utf-8")
        self.assertIn("douyin_profile_dir", patch_text)
        self.assertIn("launch_persistent_context", patch_text)
        self.assertIn("ensure_logged_in_for_upload", patch_text)
        self.assertIn("account_file.parent.mkdir(parents=True, exist_ok=True)", patch_text)
        self.assertIn('input[type="file"][accept*="video"]', patch_text)


if __name__ == "__main__":
    unittest.main()
