from __future__ import annotations

import importlib.util
import subprocess
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "groupbuy_browser.py"
SPEC = importlib.util.spec_from_file_location("groupbuy_browser", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def snapshot(descending=True):
    yesterday = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=1)).date().isoformat()
    return {
        "url": "https://www.life-data.cn/product/my/chain/list?groupid=1000000000000000",
        "checked": ["昨天"], "periods": [f"{yesterday} ～ {yesterday}"], "loading": False,
        "headers": [
            {"index": "8", "text": "商品成交券数", "descending": False, "ascending": False},
            {"index": "3", "text": "商品名称", "descending": False, "ascending": False},
            {"index": "5", "text": "商品成交金额\n(元)", "descending": descending, "ascending": not descending},
        ],
        "rows": [
            {"cells": [{"index": "8", "text": "120"},
                       {"index": "3", "text": "【测试商场】示例卡券甲\n\nID: 1000000000000001"},
                       {"index": "5", "text": "¥12,345.67"}]},
            {"cells": [{"index": "8", "text": "230"},
                       {"index": "3", "text": "示例卡券乙\nID: 1000000000000002"},
                       {"index": "5", "text": "¥1,234.56"}]},
        ],
    }


def fourteen_column_snapshot():
    data = snapshot()
    labels = ["商品名称", "商品价格", "商品访问人数", "商品成交券数", "商品成交金额", "商品成交人数",
              "新客成交金额", "新客成交人数", "访问-成交转化率", "商品核销券数", "商品核销金额",
              "商品退款券数", "商品退款金额", "操作"]
    data["headers"] = [{"index": str(index), "text": text, "descending": index == 5, "ascending": False}
                       for index, text in enumerate(labels, 1)]
    data["rows"] = []
    for index in range(10):
        cells = [f"测试卡券{index + 1}\n\nID: {1000000000000001 + index}", "¥10.00\n¥20.00", "1,000",
                 str(100 - index), f"¥{1000 - 90 * index:,.2f}", str(90 - index),
                 f"¥{10 + index:.2f}", str(10 + index), "9.00%", "20", "¥200.00", "5", "¥50.00", "查看详情"]
        data["rows"].append({"cells": [{"index": str(position), "text": text}
                                        for position, text in enumerate(cells, 1)]})
    return data


class BrowserTests(unittest.TestCase):
    def test_json_footer_and_stringified_eval_output(self):
        self.assertEqual(MODULE.parse_cli_output('{"entries": []}\n\n  Update available: v1.8.7 → v1.8.8\n'),
                         {"entries": []})
        self.assertEqual(MODULE.parse_cli_output('"{\\"ok\\":true}"\n  Update available: v1.8.8'), {"ok": True})
        self.assertEqual(MODULE.parse_cli_output('https://life.douyin.com/\n\n  Update available: v1.8.8'),
                         'https://life.douyin.com/')

    def test_call_uses_argument_array_without_shell(self):
        response = subprocess.CompletedProcess([], 0, '{"typed":true}', '')
        with patch.object(MODULE.subprocess, "run", return_value=response) as run:
            MODULE.Browser("coupon-query").type("12", 'x; $(echo unsafe)')
        self.assertEqual(run.call_args.args[0],
                         ["opencli", "browser", "coupon-query", "type", "12", 'x; $(echo unsafe)'])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_structured_cli_error_is_not_success(self):
        response = subprocess.CompletedProcess([], 0, '{"error":{"code":"stale_ref"}}', '')
        with patch.object(MODULE.subprocess, "run", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "stale_ref"):
                MODULE.Browser("test").click("12")

    def test_find_returns_empty_only_when_selector_not_found(self):
        missing = subprocess.CompletedProcess([], 1, '{"error":{"code":"selector_not_found"}}', '')
        with patch.object(MODULE.subprocess, "run", return_value=missing):
            self.assertEqual(MODULE.Browser("test").find(".byted-radio-tag"), {"entries": [], "matches_n": 0})
        for code in ("invalid_selector", "stale_ref", "detached_mid_command"):
            response = subprocess.CompletedProcess([], 1, '{"error":{"code":"' + code + '"}}', '')
            with patch.object(MODULE.subprocess, "run", return_value=response):
                with self.assertRaisesRegex(RuntimeError, code):
                    MODULE.Browser("test").find(".byted-radio-tag")

    def test_poll_retries_detached_read_and_returns_structured_value(self):
        read = unittest.mock.Mock(side_effect=[RuntimeError("detached_mid_command"), None, {"ready": True}])
        with patch.object(MODULE.time, "sleep") as sleep:
            self.assertEqual(MODULE.poll(read), {"ready": True})
        self.assertEqual(read.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_poll_does_not_retry_other_errors(self):
        read = unittest.mock.Mock(side_effect=RuntimeError("selector_ambiguous"))
        with patch.object(MODULE.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "selector_ambiguous"):
                MODULE.poll(read)
        self.assertEqual(read.call_count, 1)
        sleep.assert_not_called()

    def test_detached_open_verifies_url_without_repeating_navigation(self):
        browser = MODULE.Browser("test")
        target = "https://life.douyin.com/p/goods-list?groupid=1000000000000000&industry=tobias"
        outputs = [RuntimeError("detached_mid_command"), RuntimeError("detached_mid_command"),
                   "https://life.douyin.com/p/home?groupid=1000000000000000", target,
                   [{"page": "active-bound", "url": target, "active": True}]]
        with patch.object(browser, "call", side_effect=outputs) as call, patch.object(MODULE.time, "sleep"):
            result = browser.open(target)
        self.assertTrue(result["url_verified"])
        self.assertEqual([args.args for args in call.call_args_list],
                         [("open", target), ("get", "url"), ("get", "url"), ("get", "url"), ("tab", "list")])

    def test_detached_open_rejects_wrong_groupid(self):
        browser = MODULE.Browser("test")
        target = "https://life.douyin.com/p/goods-list?groupid=1000000000000000"
        wrong = "https://life.douyin.com/p/goods-list?groupid=1111111111111111"
        with patch.object(browser, "call", side_effect=[RuntimeError("detached_mid_command"), wrong]) as call:
            with patch.object(MODULE.time, "monotonic", side_effect=[0, 36]):
                with self.assertRaisesRegex(TimeoutError, "导航目标URL读回"):
                    browser.open(target)
        self.assertEqual(call.call_count, 2)

    def test_open_activates_returned_page_and_verifies_before_next_click(self):
        browser = MODULE.Browser("owned-query")
        target = "https://www.life-data.cn/product/my/chain/list?groupid=1000000000000000"
        opened = {"url": target, "page": "new-page"}
        inactive = [{"page": "previous", "url": target, "active": True},
                    {"page": "new-page", "url": target, "active": False}]
        active = [{"page": "new-page", "url": target, "active": True}]
        responses = [opened, inactive, {"selected": True}, inactive, active, {"clicked": True}]
        with patch.object(browser, "call", side_effect=responses) as call, patch.object(MODULE.time, "sleep"):
            self.assertEqual(browser.open(target), opened)
            browser.click("12")
        self.assertEqual([item.args for item in call.call_args_list],
                         [("open", target), ("tab", "list"), ("tab", "select", "new-page"),
                          ("tab", "list"), ("tab", "list"), ("click", "12")])

    def test_active_bound_open_does_not_select_or_create_a_tab(self):
        browser = MODULE.Browser("bound-query")
        target = "https://life.douyin.com/p/goods-list?groupid=1000000000000000"
        responses = [{"url": target, "page": "bound-page"},
                     [{"page": "bound-page", "url": target, "active": True}]]
        with patch.object(browser, "call", side_effect=responses) as call:
            browser.open(target)
        self.assertEqual([item.args for item in call.call_args_list], [("open", target), ("tab", "list")])

    def test_inactive_bound_selection_rejection_requests_new_owned_session(self):
        browser = MODULE.Browser("bound-query")
        target = "https://life.douyin.com/p/goods-list?groupid=1000000000000000"
        responses = [{"url": target, "page": "bound-page"},
                     [{"page": "bound-page", "url": target, "active": False}],
                     MODULE.OpenCLIError("tab", {"code": "bound_session_tab_mutation"})]
        with patch.object(browser, "call", side_effect=responses) as call:
            with self.assertRaisesRegex(RuntimeError, "新的 owned --session"):
                browser.open(target)
        self.assertEqual(call.call_count, 3)
        self.assertEqual(call.call_args.args, ("tab", "select", "bound-page"))

    def test_detached_open_activates_unique_matching_url_without_open_replay(self):
        browser = MODULE.Browser("owned-query")
        target = "https://www.life-data.cn/product/my/chain/list?groupid=1000000000000000"
        responses = [RuntimeError("detached_mid_command"), target,
                     [{"page": "ranking-page", "url": target, "active": False}], {"selected": True},
                     [{"page": "ranking-page", "url": target, "active": True}]]
        with patch.object(browser, "call", side_effect=responses) as call:
            result = browser.open(target)
        self.assertTrue(result["url_verified"])
        self.assertEqual([item.args for item in call.call_args_list],
                         [("open", target), ("get", "url"), ("tab", "list"),
                          ("tab", "select", "ranking-page"), ("tab", "list")])

    def test_open_without_page_rejects_duplicate_matching_urls(self):
        browser = MODULE.Browser("owned-query")
        target = "https://life.douyin.com/p/goods-list?groupid=1000000000000000"
        responses = [{"url": target}, [{"page": "a", "url": target, "active": False},
                                     {"page": "b", "url": target, "active": True}]]
        with patch.object(browser, "call", side_effect=responses) as call:
            with self.assertRaisesRegex(RuntimeError, "目标标签不唯一"):
                browser.open(target)
        self.assertEqual(call.call_count, 2)


class RankingTests(unittest.TestCase):
    def test_cells_follow_header_ids_and_money_remains_exact_in_fen(self):
        products = MODULE.parse_rank_rows(snapshot(), "销售金额", 2)
        self.assertEqual(products[0]["product_id"], "1000000000000001")
        self.assertEqual(products[0]["name"], "【测试商场】示例卡券甲")
        self.assertEqual(products[0]["coupon_count"], 120)
        self.assertEqual(products[0]["sales_amount_fen"], 1234567)
        self.assertEqual(products[1]["sales_amount_fen"], 123456)
        self.assertEqual(products[0]["metric_value"], "12345.67")

    def test_rejects_unsorted_values_and_incomplete_top_ten(self):
        data = snapshot()
        data["rows"].reverse()
        with self.assertRaisesRegex(RuntimeError, "不是降序"):
            MODULE.parse_rank_rows(data, "成交金额", 2)
        with self.assertRaisesRegex(RuntimeError, "不支持分页"):
            MODULE.parse_rank_rows(snapshot(), "成交金额", 10)

    def test_coupon_count_metric_checks_its_own_order(self):
        data = snapshot()
        data["rows"].reverse()
        products = MODULE.parse_rank_rows(data, "券数", 2)
        self.assertEqual(products[0]["metric_value"], "230")
        self.assertEqual(products[0]["sales_amount_fen"], 123456)

    def test_real_fourteen_column_shape_excludes_new_customer_and_refund_metrics(self):
        data = fourteen_column_snapshot()
        self.assertEqual(len(data["headers"]), 14)
        self.assertEqual(MODULE._header(data, "成交金额")["index"], "5")
        self.assertEqual(MODULE._header(data, "成交券数")["index"], "4")
        products = MODULE.parse_rank_rows(data, "商品成交金额", 10)
        self.assertEqual(products[0]["sales_amount_fen"], 100000)
        self.assertEqual(products[-1]["sales_amount_fen"], 19000)
        self.assertEqual(products[0]["coupon_count"], 100)
        self.assertEqual(products[-1]["rank"], 10)

    def test_prefixed_metric_names_do_not_match_exact_header(self):
        data = fourteen_column_snapshot()
        data["headers"][4]["text"] = "新客成交金额"
        with self.assertRaisesRegex(RuntimeError, "无法唯一识别成交金额"):
            MODULE._header(data, "成交金额")

    def test_select_verifies_period_loading_and_descending(self):
        ready = snapshot(False)
        loading = deepcopy(ready)
        loading["loading"] = True
        wrong_date = deepcopy(ready)
        wrong_date["periods"] = ["2000-01-01 ～ 2000-01-01"]

        class FakeBrowser:
            def __init__(self):
                self.clicks = []
                self.snapshots = iter([wrong_date, loading, ready, ready, snapshot()])

            def open(self, url):
                self.url = url

            def find(self, css):
                if css == ".byted-radio-tag":
                    return {"entries": [{"ref": 9, "text": "昨天", "visible": True}]}
                self.sort_selector = css
                return {"entries": [{"ref": 18, "visible": True}]}

            def click(self, ref):
                self.clicks.append(ref)

            def eval(self, script):
                return next(self.snapshots)

        browser = FakeBrowser()
        with patch.object(MODULE.time, "sleep"):
            result = MODULE.select_rank(browser, "1000000000000000", "昨天", "销售金额", 2)
        self.assertEqual(len(browser.clicks), 2)
        self.assertEqual(browser.sort_selector, 'th[aria-colindex="5"] button')
        self.assertEqual(result["source"]["sort"], "descending")
        self.assertEqual(result["products"][0]["rank"], 1)
        self.assertFalse(result["source"]["loading"])

    def test_invalid_scope_rejected_before_browser(self):
        for date, metric, limit in (("今天", "成交金额", 10), ("昨天", "成交金额", 11), ("昨天", "收入", 10)):
            with self.assertRaises(ValueError):
                MODULE.select_rank(None, "1000000000000000", date, metric, limit)


if __name__ == "__main__":
    unittest.main()
