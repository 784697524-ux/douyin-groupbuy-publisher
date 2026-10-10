from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "douyin_groupbuy_pipeline.py"
SPEC = importlib.util.spec_from_file_location("pipeline_flow_module", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
RANK_MODULE = sys.modules[MODULE.select_rank.__module__]
FORWARD_TEST = ROOT.parent / "forward-test"
GROUP_ID = "1000000000000000"
LOCATION = "测试商场国内位置"


def selection_fixture():
    return {
        "groupid": GROUP_ID, "date_filter": "昨天", "metric": "商品成交金额", "limit": 10,
        "source": {"sort_active": True, "sort": "descending"},
        "timings": {"total_seconds": 0.1},
        "products": [{"rank": index, "product_id": str(1000000000000000 + index),
                      "id": str(1000000000000000 + index), "name": f"测试卡券{index}",
                      "sales_amount_fen": 100000 - 1000 * index}
                     for index in range(1, 11)],
    }


def detail_fixture(product_id):
    return {
        "product_id": product_id, "verified": True,
        "url": f"https://life.douyin.com/p/tobias/goods/detail?groupid={GROUP_ID}&product_id={product_id}",
        "detail_text": "完整的测试卡券说明",
        "fields": {"商品名称": "第三张测试卡券", "售价": "19.90元", "可用日期": "2026.10.01 至 2026.10.31",
                   "限购规则": "每人限购1张", "退款规则描述": "过期可退"},
    }


def parse(*args):
    with patch.object(sys, "argv", [str(SCRIPT), *map(str, args)]):
        return MODULE.parse_args()


class PipelineFlowTests(unittest.TestCase):
    def setUp(self):
        FORWARD_TEST.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="pipeline-", dir=FORWARD_TEST)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.selection = self.folder / "selection.json"
        self.brief = self.folder / "brief"

    def write_selection(self, data):
        self.selection.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def prepare_third_brief(self):
        data = selection_fixture()
        data["products"][2]["laike_detail"] = detail_fixture(data["products"][2]["product_id"])
        self.write_selection(data)
        args = parse("brief", "--selection", self.selection, "--product-index", "3",
                     "--output-dir", self.brief, "--location", LOCATION)
        with redirect_stdout(io.StringIO()):
            MODULE.build_brief(args)

    def create_four_images(self):
        images = []
        for index in range(4):
            image = self.brief / "images" / f"{index + 1:02d}.png"
            image.write_bytes(b"isolated fixture; never uploaded")
            images.append(str(image))
        return images

    def test_select_detail_index_three_reads_only_third_product(self):
        args = parse("select", "--groupid", GROUP_ID, "--session", "forward-test", "--output", self.selection,
                     "--date", "昨天", "--metric", "商品成交金额", "--limit", "10", "--detail-index", "3")
        browser = object()
        with patch.object(MODULE, "Browser", return_value=browser) as browser_factory:
            with patch.object(MODULE, "select_rank", return_value=selection_fixture()) as rank:
                with patch.object(MODULE, "read_detail", side_effect=lambda _, __, product: detail_fixture(product)) as detail:
                    with redirect_stdout(io.StringIO()):
                        MODULE.select_products(args)
        browser_factory.assert_called_once_with("forward-test")
        rank.assert_called_once_with(browser, GROUP_ID, "昨天", "商品成交金额", 10)
        detail.assert_called_once_with(browser, GROUP_ID, "1000000000000003")
        saved = json.loads(self.selection.read_text())
        self.assertEqual(saved["detail_index"], 3)
        self.assertTrue(saved["products"][2]["laike_detail"]["verified"])
        self.assertNotIn("laike_detail", saved["products"][0])

    def test_run_uses_product_index_for_detail_and_passes_four_images_to_dry_run(self):
        image_dir = self.folder / "ready-images"
        image_dir.mkdir()
        images = []
        for index in range(4):
            path = image_dir / f"{index + 1:02d}.png"
            path.write_bytes(b"fixture")
            images.append(str(path))
        args = parse("run", "--groupid", GROUP_ID, "--session", "forward-test", "--output", self.selection,
                     "--product-index", "3", "--output-dir", self.brief, "--location", LOCATION,
                     "--account", "test_creator", "--images", *images, "--publish", "--dry-run")
        browser = object()
        output = io.StringIO()
        with patch.object(MODULE, "Browser", return_value=browser):
            with patch.object(MODULE, "select_rank", return_value=selection_fixture()):
                with patch.object(MODULE, "read_detail", side_effect=lambda _, __, product: detail_fixture(product)) as detail:
                    with patch.object(MODULE, "run", side_effect=AssertionError("Runtime must not run")) as runtime:
                        with redirect_stdout(output):
                            MODULE.run_pipeline(args)
        detail.assert_called_once_with(browser, GROUP_ID, "1000000000000003")
        runtime.assert_not_called()
        meta = json.loads((self.brief / "publish_meta.json").read_text())
        self.assertEqual(meta["product_index"], 3)
        self.assertEqual(meta["location"], LOCATION)
        caption = (self.brief / "caption.txt").read_text()
        prompts = (self.brief / "image_prompts.md").read_text()
        self.assertIn(LOCATION, caption)
        self.assertEqual(prompts.count(LOCATION), 4)
        prepared = json.loads(output.getvalue().splitlines()[-1])
        command = prepared["command"]
        self.assertEqual(prepared["status"], "prepared_not_published")
        self.assertEqual(command[command.index("--images") + 1:command.index("--title")], images)
        self.assertEqual(command[command.index("--account") + 1], "test_creator")
        self.assertEqual(command[command.index("--location") + 1], LOCATION)
        self.assertEqual(command[command.index("--notef") + 1], str(self.brief / "caption.txt"))

    def test_saved_selection_detail_command_uses_third_product_and_saved_group(self):
        self.write_selection(selection_fixture())
        out = self.folder / "detail.json"
        args = parse("detail", "--selection", self.selection, "--product-index", "3", "--output", out,
                     "--groupid", "999", "--session", "forward-test")
        browser = object()
        with patch.object(MODULE, "Browser", return_value=browser):
            with patch.object(MODULE, "read_detail", side_effect=lambda _, __, product: detail_fixture(product)) as detail:
                with redirect_stdout(io.StringIO()):
                    MODULE.query_detail(args)
        detail.assert_called_once_with(browser, GROUP_ID, "1000000000000003")
        self.assertEqual(json.loads(out.read_text())["product_id"], "1000000000000003")
        self.assertEqual(json.loads(self.selection.read_text())["detail_index"], 3)

    def test_brief_refuses_unverified_requested_product(self):
        self.write_selection(selection_fixture())
        args = parse("brief", "--selection", self.selection, "--product-index", "3",
                     "--output-dir", self.brief, "--location", LOCATION)
        with self.assertRaises(SystemExit):
            MODULE.build_brief(args)
        self.assertFalse(self.brief.exists())

    def test_brief_refuses_missing_detail_even_if_source_flag_is_absent(self):
        data = selection_fixture()
        data.pop("source")
        self.write_selection(data)
        args = parse("brief", "--selection", self.selection, "--product-index", "3",
                     "--output-dir", self.brief, "--location", LOCATION)
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                MODULE.build_brief(args)

    def test_publish_dry_run_validates_caption_file_without_calling_runtime(self):
        self.prepare_third_brief()
        self.create_four_images()
        (self.brief / "caption.txt").unlink()
        args = parse("publish", "--account", "test_creator", "--work-dir", self.brief, "--dry-run")
        with patch.object(MODULE, "run", side_effect=AssertionError("Runtime must not run")) as runtime:
            with self.assertRaises((SystemExit, FileNotFoundError)):
                with redirect_stdout(io.StringIO()):
                    MODULE.publish_note(args)
        runtime.assert_not_called()

    def test_rank_provenance_checks_hostname_path_and_groupid(self):
        expected_date = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=1)).date().isoformat()
        wrong_urls = [f"https://example.com/product/my/chain/list?groupid={GROUP_ID}",
                      f"https://www.life-data.cn/wrong-list?groupid={GROUP_ID}",
                      "https://www.life-data.cn/product/my/chain/list?groupid=999"]
        for url in wrong_urls:
            with self.subTest(url=url):
                browser = Mock()
                browser.find.return_value = {"entries": [{"visible": True, "text": "昨天", "ref": 1}]}
                browser.eval.return_value = {"url": url, "checked": ["昨天"], "loading": False,
                                             "periods": [f"{expected_date} ～ {expected_date}"], "rows": [1]}
                with patch.object(RANK_MODULE.time, "monotonic", side_effect=[0, 0, 0, 36]):
                    with self.assertRaises(TimeoutError):
                        MODULE.select_rank(browser, GROUP_ID)


if __name__ == "__main__":
    unittest.main()
