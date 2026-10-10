#!/usr/bin/env python3
"""OpenCLI browser access and verified Shengyijing coupon rankings."""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo


RANKING_URL = "https://www.life-data.cn/product/my/chain/list?groupid={groupid}"


class OpenCLIError(RuntimeError):
    def __init__(self, command: str, error: Any) -> None:
        self.code = error.get("code") if isinstance(error, dict) else None
        super().__init__(f"OpenCLI {command}失败: {error}")


def parse_cli_output(output: str) -> Any:
    text = output.strip()
    try:
        value, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        return re.split(r"\n\s*Update available:", text, maxsplit=1)[0].rstrip()
    if isinstance(value, str):
        try:
            value, _ = json.JSONDecoder().raw_decode(value.strip())
        except json.JSONDecodeError:
            pass
    return value


def poll(predicate: Callable[[], Any], label: str = "condition", max_seconds: float = 35,
         interval: float = 0.5) -> Any:
    deadline = time.monotonic() + min(max_seconds, 35)
    while True:
        try:
            value = predicate()
        except RuntimeError as error:
            if "detached_mid_command" not in str(error):
                raise
            value = None
        if value:
            return value
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"等待{label}超时")
        time.sleep(min(interval, remaining))


class Browser:
    def __init__(self, session: str, executable: str = "opencli") -> None:
        self.session = session
        self.executable = executable

    def call(self, *args: str) -> Any:
        result = subprocess.run(
            [self.executable, "browser", self.session, *map(str, args)],
            capture_output=True, text=True, timeout=35,
        )
        value = parse_cli_output(result.stdout)
        if result.returncode or isinstance(value, dict) and value.get("error"):
            error = value.get("error") if isinstance(value, dict) else result.stderr.strip() or value
            raise OpenCLIError(args[0], error)
        return value

    def eval(self, script: str) -> Any:
        return self.call("eval", script)

    def find(self, css: str, limit: int = 20, text_max: int = 200) -> dict:
        try:
            return self.call("find", "--css", css, "--limit", str(limit), "--text-max", str(text_max))
        except OpenCLIError as error:
            if error.code != "selector_not_found":
                raise
            return {"entries": [], "matches_n": 0}

    def click(self, target: str | int, nth: int | None = None) -> dict:
        args = ["click", str(target)]
        if nth is not None:
            args.extend(["--nth", str(nth)])
        return self.call(*args)

    def type(self, target: str | int, text: str, nth: int | None = None) -> dict:
        args = ["type", str(target), text]
        if nth is not None:
            args.extend(["--nth", str(nth)])
        return self.call(*args)

    def get(self, kind: str, target: str | int | None = None, nth: int | None = None) -> Any:
        args = ["get", kind]
        if target is not None:
            args.append(str(target))
        if nth is not None:
            args.extend(["--nth", str(nth)])
        return self.call(*args)

    def open(self, url: str) -> Any:
        try:
            opened = self.call("open", url)
        except RuntimeError as error:
            if "detached_mid_command" not in str(error):
                raise
            expected = urlparse(url)

            def target_url() -> str | None:
                current = self.get("url")
                actual = urlparse(current)
                matches = (actual.hostname == expected.hostname and (actual.path or "/") == (expected.path or "/")
                           and parse_qs(actual.query).get("groupid") == parse_qs(expected.query).get("groupid"))
                return current if matches else None

            current = poll(target_url, "导航目标URL读回")
            opened = {"url": current, "url_verified": True, "recovered_from": "detached_mid_command"}

        tabs = self.call("tab", "list")
        page = opened.get("page") if isinstance(opened, dict) else None
        target_url = opened.get("url", url) if isinstance(opened, dict) else url
        matches = [tab for tab in tabs if str(tab["page"]) == str(page)] if page is not None else \
            [tab for tab in tabs if tab.get("url") == target_url]
        if len(matches) != 1:
            raise RuntimeError("导航目标标签不唯一或已关闭，拒绝选择其他标签")
        tab = matches[0]
        if not tab.get("active"):
            page = str(tab["page"])
            try:
                self.call("tab", "select", page)
            except RuntimeError as error:
                raise RuntimeError(
                    f"导航标签未激活且tab select被拒绝：{error}。请使用新的 owned --session（不要 bind）后重试"
                ) from error

            def active_tab() -> dict | None:
                return next((item for item in self.call("tab", "list")
                             if str(item["page"]) == page and item.get("active")), None)

            poll(active_tab, "导航目标标签激活读回")
        return opened

    def poll(self, predicate: Callable[[], Any], label: str = "condition", max_seconds: float = 35,
             interval: float = 0.5) -> Any:
        return poll(predicate, label, max_seconds, interval)


RANK_SNAPSHOT = r"""(() => {
    const visible = el => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
    };
    const headers = Array.from(document.querySelectorAll('th[aria-colindex]')).filter(visible);
    return {
        url: location.href,
        checked: Array.from(document.querySelectorAll('.byted-radio-tag-checked')).filter(visible)
            .map(el => el.innerText.trim()),
        periods: (document.body.innerText.match(/\d{4}-\d{2}-\d{2}\s*[～~至]\s*\d{4}-\d{2}-\d{2}/g) || []),
        loading: Array.from(document.querySelectorAll('.byted-loading-mask-show')).some(visible),
        headers: headers.map(el => ({
            index: el.getAttribute('aria-colindex'), text: el.innerText.trim(),
            descending: !!el.querySelector('.byted-TableQueryTrigger-Down_active'),
            ascending: !!el.querySelector('.byted-TableQueryTrigger-Up_active')
        })),
        rows: Array.from(document.querySelectorAll('tbody tr')).filter(visible).map(tr => ({
            cells: Array.from(tr.querySelectorAll('td')).map((td, i) => ({
                index: td.getAttribute('aria-colindex') || (headers[i] && headers[i].getAttribute('aria-colindex')),
                text: td.innerText.trim()
            }))
        }))
    };
})()"""


def _metric_kind(metric: str) -> tuple[str, str]:
    if metric in ("商品成交金额", "成交金额", "销售金额", "卡券销售金额"):
        return "成交金额", "商品成交金额"
    if metric in ("商品成交券数", "成交券数", "券数"):
        return "成交券数", "商品成交券数"
    raise ValueError("仅支持成交金额或成交券数")


def _header(snapshot: dict, key: str) -> dict:
    def normalized(text: str) -> str:
        compact = re.sub(r"\s+", "", text)
        return re.sub(r"[（(](?:元|张)[）)]$", "", compact)

    matches = [header for header in snapshot["headers"] if normalized(header["text"]) in (key, f"商品{key}")]
    if len(matches) != 1:
        raise RuntimeError(f"榜单表头无法唯一识别{key}")
    return matches[0]


def _period_matches(snapshot: dict, expected_date: str) -> bool:
    return any(re.findall(r"\d{4}-\d{2}-\d{2}", period) == [expected_date, expected_date]
               for period in snapshot["periods"])


def _number(text: str) -> Decimal:
    value = text.strip().replace(",", "").replace("¥", "").replace("￥", "").removesuffix("元").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise RuntimeError(f"榜单指标不是可解析数值: {text}")
    return Decimal(value)


def parse_rank_rows(snapshot: dict, metric: str, limit: int) -> list[dict]:
    metric_key, canonical_metric = _metric_kind(metric)
    metric_header = _header(snapshot, metric_key)
    amount_header = _header(snapshot, "成交金额")
    count_header = _header(snapshot, "成交券数")
    products = []
    seen = set()
    values = []
    for row in snapshot["rows"]:
        cells = {str(cell["index"]): cell["text"] for cell in row["cells"]}
        product_cells = [text for text in cells.values() if re.search(r"\bID\s*[:：]\s*\d{10,}", text, re.I)]
        if not product_cells:
            continue
        if len(product_cells) != 1:
            raise RuntimeError("榜单商品行包含多个商品ID")
        product_text = product_cells[0]
        match = re.search(r"\bID\s*[:：]\s*(\d{10,})", product_text, re.I)
        product_id = match.group(1)
        if product_id in seen:
            raise RuntimeError(f"榜单商品ID重复: {product_id}")
        seen.add(product_id)
        name = product_text[:match.start()].strip()
        if not name:
            raise RuntimeError(f"榜单缺少商品名称: {product_id}")
        amount = _number(cells[str(amount_header["index"])])
        count = _number(cells[str(count_header["index"])])
        if count != count.to_integral_value():
            raise RuntimeError("成交券数不是整数")
        selected_value = _number(cells[str(metric_header["index"])])
        values.append(selected_value)
        products.append({
            "rank": len(products) + 1, "id": product_id, "product_id": product_id, "name": name,
            "metric": canonical_metric, "metric_value": str(selected_value),
            "sales_amount_yuan": float(amount), "sales_amount_fen": int(amount * 100),
            "coupon_count": int(count),
        })
        if len(products) == limit:
            break
    if len(products) < limit:
        raise RuntimeError(f"当前页只有{len(products)}张卡券，无法取得前{limit}；本命令不支持分页")
    if any(left < right for left, right in zip(values, values[1:])):
        raise RuntimeError("榜单数值不是降序，拒绝报告排名")
    return products


def select_rank(browser: Browser, groupid: str, date_filter: str = "昨天",
                metric: str = "商品成交金额", limit: int = 10) -> dict:
    if date_filter != "昨天":
        raise ValueError("日期筛选当前仅支持昨天")
    if not 1 <= limit <= 10:
        raise ValueError("limit必须为1至10；不支持分页")
    metric_key, canonical_metric = _metric_kind(metric)
    expected_date = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=1)).date().isoformat()
    url = RANKING_URL.format(groupid=groupid)
    timings = {}
    started = time.monotonic()
    browser.open(url)
    date_entries = poll(
        lambda: [entry for entry in browser.find(".byted-radio-tag")["entries"]
                 if entry.get("visible") and entry.get("text", "").strip() == date_filter], "昨日筛选标签",
    )
    if len(date_entries) != 1:
        raise RuntimeError("昨日日期标签不唯一")
    browser.click(date_entries[0]["ref"])

    def date_ready() -> dict | None:
        snapshot = browser.eval(RANK_SNAPSHOT)
        current_url = urlparse(snapshot["url"])
        correct_page = (current_url.netloc == "www.life-data.cn" and current_url.path == "/product/my/chain/list"
                        and parse_qs(current_url.query).get("groupid") == [str(groupid)])
        return snapshot if (correct_page and date_filter in snapshot["checked"] and _period_matches(snapshot, expected_date)
                            and not snapshot["loading"] and snapshot["rows"]) else None

    snapshot = poll(date_ready, "昨日日期与榜单加载")
    timings["date_seconds"] = round(time.monotonic() - started, 3)
    sort_started = time.monotonic()
    metric_header = _header(snapshot, metric_key)
    for _ in range(2):
        if metric_header["descending"] and not metric_header["ascending"]:
            break
        selector = f'th[aria-colindex="{metric_header["index"]}"] button'
        targets = browser.find(selector)["entries"]
        targets = [entry for entry in targets if entry.get("visible")]
        if len(targets) != 1:
            raise RuntimeError(f"{metric_key}排序按钮不唯一")
        browser.click(selector)
        previous_direction = (metric_header["descending"], metric_header["ascending"])

        def sort_ready() -> dict | None:
            current = date_ready()
            if not current:
                return None
            header = _header(current, metric_key)
            return current if (header["descending"], header["ascending"]) != previous_direction else None

        snapshot = poll(sort_ready, "排序状态更新与榜单加载")
        metric_header = _header(snapshot, metric_key)
    if not metric_header["descending"] or metric_header["ascending"]:
        raise RuntimeError(f"无法确认{metric_key}降序激活")
    products = parse_rank_rows(snapshot, canonical_metric, limit)
    timings["sort_and_extract_seconds"] = round(time.monotonic() - sort_started, 3)
    timings["total_seconds"] = round(time.monotonic() - started, 3)
    return {
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "groupid": str(groupid), "date_filter": date_filter, "metric": canonical_metric, "limit": limit,
        "ranking_url": snapshot["url"], "products": products,
        "source": {"date": expected_date, "period": f"{expected_date} ～ {expected_date}",
                   "sort": "descending", "metric": canonical_metric, "ranking_url": snapshot["url"],
                   "date_checked": date_filter, "sort_active": True, "loading": False},
        "timings": timings,
    }
