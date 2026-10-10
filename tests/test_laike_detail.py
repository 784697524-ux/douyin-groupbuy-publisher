from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "laike_detail.py"
SPEC = importlib.util.spec_from_file_location("laike_detail", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

PRODUCT_ID = "1000000000000001"
URL = f"https://life.douyin.com/p/tobias/goods/detail?groupid=123&product_id={PRODUCT_ID}"


def snapshot() -> dict:
    return {
        "url": URL, "detail_text": "商品名称\n银泰逛吃卡\n售价\n19.9元",
        "fields": {
            "商品名称": "银泰逛吃卡", "售价": "19.9元\n2折", "可用日期": "2026.10.10 至 2026.10.31",
            "限购规则": "每人限购1张", "退款规则描述": "过期可退",
            "采购价": "1元", "其他说明信息": f"凭券使用\n商品ID：{PRODUCT_ID}\n排名第一\n销量100",
            "适用门店": "已选择1家适用门店\n门店\n测试商场\n测试地址\n1",
            "组品搭配": "餐饮（3选1）\n测试餐品\n采购价：￥7\n饮品（全部可享）\n测试饮品\n采购价：￥0",
        },
        "image_urls": ["https://example.com/product.png"],
        "loading": False,
        "poi_readiness": {"expected_count": 1, "displayed_count": 1, "rows": 1},
    }


class DetailTests(unittest.TestCase):
    def test_identity_requires_detail_route_and_exact_id(self) -> None:
        self.assertTrue(MODULE.is_product_detail_url(URL, PRODUCT_ID))
        self.assertFalse(MODULE.is_product_detail_url(URL, "1000000000000002"))
        self.assertFalse(MODULE.is_product_detail_url(URL.replace("/tobias/goods/detail", "/goods-list"), PRODUCT_ID))
        self.assertFalse(MODULE.is_product_detail_url(URL.replace("life.douyin.com", "example.com"), PRODUCT_ID))
        self.assertTrue(MODULE.is_product_detail_url(URL, PRODUCT_ID, "123"))
        self.assertFalse(MODULE.is_product_detail_url(URL, PRODUCT_ID, "456"))

    def test_blank_or_wrong_product_is_not_verified(self) -> None:
        with self.assertRaises(RuntimeError):
            MODULE.verify_detail(snapshot(), "999")
        incomplete = snapshot()
        incomplete["fields"] = {}
        with self.assertRaises(RuntimeError):
            MODULE.verify_detail(incomplete, PRODUCT_ID)

    def test_public_copy_keeps_rules_and_excludes_backend_facts(self) -> None:
        result = MODULE.build_product_copy({"product_id": PRODUCT_ID}, snapshot(), "合肥滨湖银泰百货")
        caption = result["public"]["caption"]
        self.assertIn("19.9元", caption)
        self.assertIn("2026.10.10 至 2026.10.31", caption)
        self.assertIn("每人限购1张", caption)
        self.assertIn("过期可退", caption)
        self.assertIn("餐饮（3选1）；饮品（全部可享）", caption)
        self.assertIn("测试商场\n测试地址", caption)
        for private in (PRODUCT_ID, "采购价", "1元", "排名第一", "销量100", "测试餐品", "测试饮品", "2折", "已选择"):
            self.assertNotIn(private, caption)
        self.assertEqual(len(result["image_prompts"]), 4)

    def test_single_applicable_store_can_supply_location(self) -> None:
        result = MODULE.build_product_copy({"product_id": PRODUCT_ID}, snapshot())
        self.assertEqual(result["public"]["location"], "测试商场")

    def test_existing_same_product_detail_is_reused(self) -> None:
        class Browser:
            def eval(self, js):
                return URL if js == "location.href" else snapshot()

            def poll(self, predicate, **kwargs):
                return predicate()

            def open(self, url):
                raise AssertionError("Should reuse the already verified detail page")

        result = MODULE.read_detail(Browser(), "123", PRODUCT_ID)
        self.assertTrue(result["verified"])
        self.assertEqual(result["product_id"], PRODUCT_ID)
        self.assertEqual(result["recovery"], "none")

    def test_search_waits_for_exact_view_and_survives_detached_navigation(self) -> None:
        class Browser:
            def __init__(self):
                self.url = "https://www.life-data.cn/"
                self.value = ""
                self.view_polls = 0
                self.clicked = []

            def open(self, url):
                self.url = url
                raise RuntimeError("detached_mid_command")

            def eval(self, js):
                if js == "location.href":
                    return self.url
                if js == MODULE.DETAIL_SNAPSHOT_JS:
                    return snapshot()
                if "_view" in js:
                    self.view_polls += 1
                    return self.view_polls > 1
                if "?.value" in js:
                    # OpenCLI decodes a bare all-digit result as int; an object retains its string value.
                    return {"value": self.value} if js.startswith("({value:") else int(self.value)
                if ".map(el => el.innerText.trim())" in js:
                    return ["查询"]
                return True

            def find(self, selector):
                return {"entries": [{"ref": "input-ref", "visible": True}]}

            def call(self, command, target, value):
                if command != "fill":
                    raise AssertionError(command)
                self.value = value

            def click(self, target):
                self.clicked.append(target)

            def poll(self, predicate, **kwargs):
                for _ in range(3):
                    result = predicate()
                    if result:
                        return result
                raise TimeoutError()

        browser = Browser()
        result = MODULE.read_detail(browser, "123", PRODUCT_ID)
        self.assertTrue(result["verified"])
        self.assertEqual(browser.clicked, [MODULE.QUERY, f'[id="{PRODUCT_ID}_view"]'])
        self.assertEqual(browser.view_polls, 2)

    def test_blank_detail_opens_one_fresh_tab_with_verified_url(self) -> None:
        class Browser:
            def __init__(self):
                self.calls = []
                self.poll_count = 0
                self.fresh_selected = False

            def eval(self, js):
                if js == "location.href":
                    return URL
                return snapshot() if self.fresh_selected else {"url": URL, "fields": {}, "detail_text": "智能助手"}

            def poll(self, predicate, **kwargs):
                self.poll_count += 1
                if self.poll_count == 1:
                    self.initial_wait = kwargs["max_seconds"]
                    raise TimeoutError("blank main")
                return predicate()

            def call(self, *args):
                self.calls.append(args)
                if args[1] == "select":
                    self.fresh_selected = True
                return {"page": 17} if args[1] == "new" else {}

        browser = Browser()
        result = MODULE.read_detail(browser, "123", PRODUCT_ID)
        self.assertEqual(browser.initial_wait, 6)
        self.assertEqual(browser.calls, [("tab", "new", URL), ("tab", "select", "17")])
        self.assertEqual(result["recovery"], "fresh_tab")

    def test_group_mismatch_is_not_verified(self) -> None:
        with self.assertRaises(RuntimeError):
            MODULE.verify_detail(snapshot(), PRODUCT_ID, "wrong-group")

    def test_async_poi_zero_waits_until_reference_count_matches(self) -> None:
        initial = snapshot()
        initial["fields"]["适用门店"] = "已选择0家适用门店"
        initial["poi_readiness"] = {"expected_count": 1, "displayed_count": 0, "rows": 0}

        class Browser:
            def __init__(self):
                self.snapshots = iter((initial, snapshot()))

            def eval(self, js):
                return next(self.snapshots)

        browser = Browser()
        self.assertIsNone(MODULE._ready_snapshot(browser, PRODUCT_ID, "123"))
        self.assertIsNotNone(MODULE._ready_snapshot(browser, PRODUCT_ID, "123"))
        with self.assertRaises(RuntimeError):
            MODULE.verify_detail(initial, PRODUCT_ID, "123")

    def test_real_zero_stores_remains_zero_and_completes(self) -> None:
        result = snapshot()
        result["fields"]["适用门店"] = "已选择0家适用门店"
        result["poi_readiness"] = {"expected_count": 0, "displayed_count": 0, "rows": 0}
        verified = MODULE.verify_detail(result, PRODUCT_ID, "123")
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["fields"]["适用门店"], "已选择0家适用门店")

    def test_paged_store_table_only_needs_first_page_rows(self) -> None:
        result = snapshot()
        result["poi_readiness"] = {"expected_count": 12, "displayed_count": 12, "rows": 10}
        self.assertTrue(MODULE.verify_detail(result, PRODUCT_ID, "123")["verified"])

    def test_loaded_name_with_pending_stores_keeps_same_tab(self) -> None:
        pending = snapshot()
        pending["poi_readiness"] = {"expected_count": 1, "displayed_count": 0, "rows": 0}

        class Browser:
            def __init__(self):
                self.poll_count = 0

            def eval(self, js):
                if js == "location.href":
                    return URL
                return pending if self.poll_count == 1 else snapshot()

            def poll(self, predicate, **kwargs):
                self.poll_count += 1
                if self.poll_count == 1:
                    raise TimeoutError("stores pending")
                return predicate()

            def call(self, *args):
                raise AssertionError("Pending stores should not open a fresh tab")

        result = MODULE.read_detail(Browser(), "123", PRODUCT_ID)
        self.assertEqual(result["recovery"], "none")
        self.assertEqual(result["poi_readiness"]["displayed_count"], 1)


if __name__ == "__main__":
    unittest.main()
