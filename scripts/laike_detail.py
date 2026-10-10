#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlencode, urlparse


GOODS_LIST_URL = "https://life.douyin.com/p/goods-list"
DETAIL_PATH = "/p/tobias/goods/detail"
INPUT = 'input[placeholder="请输入"]'
QUERY = 'button[type="submit"]'
PUBLIC_FIELDS = (
    "售价", "适用门店", "商品售卖日期", "可用日期", "限购规则", "退款规则描述", "其他说明信息",
)

DETAIL_SNAPSHOT_JS = r"""(() => {
    const root = document.querySelector('main.life-core-layout-content');
    if (!root) return null;
    const wanted = ['商品名称', '售价', '适用门店', '组品搭配', '商品售卖日期', '可用日期', '限购规则', '退款规则描述', '其他说明信息'];
    const fields = {};
    const normalize = text => (text || '').trim().replace(/[:：\s]+$/, '');
    const visible = el => {
        const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    };
    for (const label of root.querySelectorAll('label')) {
        const name = normalize(label.innerText);
        if (!wanted.includes(name)) continue;
        let box = label.parentElement;
        while (box && box !== root) {
            if (box.querySelectorAll('label').length === 1 &&
                normalize(box.innerText) !== name) break;
            box = box.parentElement;
        }
        if (!box || box === root) continue;
        const text = (box.innerText || '').trim();
        fields[name] = text.startsWith(label.innerText.trim())
            ? text.slice(label.innerText.trim().length).replace(/^[:：\s]+/, '').trim() : '';
    }
    const poi = root.querySelector('[data-id="poiList"]');
    const reference = root.querySelector('[data-id="actualAmount"]')?.getAttribute('poilist');
    const refs = reference == null ? null : reference.split(',').map(value => value.trim()).filter(Boolean);
    const expected = refs && refs.every(value => /^\d+$/.test(value)) ? new Set(refs).size : null;
    const displayed = (poi?.innerText || '').match(/已选择(\d+)家适用门店/);
    return {
        url: location.href,
        detail_text: root.innerText.trim(),
        fields,
        loading: [...root.querySelectorAll('.byted-loading-mask-show,[aria-busy="true"]')].some(visible),
        poi_readiness: {
            expected_count: expected,
            displayed_count: displayed ? Number(displayed[1]) : null,
            rows: poi?.querySelectorAll('tbody tr').length || 0
        },
        image_urls: [...new Set([...root.querySelectorAll('img')]
            .map(img => img.currentSrc || img.src).filter(Boolean))]
    };
})()"""


def is_product_detail_url(url: str, product_id: str, groupid: str | None = None) -> bool:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return (
        parsed.hostname == "life.douyin.com"
        and parsed.path == DETAIL_PATH
        and query.get("product_id") == [str(product_id)]
        and (groupid is None or query.get("groupid") == [str(groupid)])
    )


def verify_detail(snapshot: dict | None, product_id: str, groupid: str | None = None) -> dict:
    if not snapshot or not is_product_detail_url(snapshot.get("url", ""), product_id, groupid):
        raise RuntimeError(f"详情页面与商品 ID 不一致：{product_id}")
    if not _snapshot_complete(snapshot):
        raise RuntimeError("商品详情或适用门店尚未完成加载")
    return {**snapshot, "product_id": str(product_id), "verified": True}


def _visible_elements(css: str) -> str:
    return "[...document.querySelectorAll(" + json.dumps(css) + ")].filter(el => el.getClientRects().length)"


def read_detail(browser, groupid: str, product_id: str) -> dict:
    product_id = str(product_id)
    if not product_id.isdigit():
        raise ValueError("商品 ID 必须是数字")
    if not is_product_detail_url(browser.eval("location.href"), product_id, groupid):
        url = GOODS_LIST_URL + "?" + urlencode({"groupid": groupid, "industry": "tobias"})
        try:
            browser.open(url)
        except RuntimeError as error:
            if "detached_mid_command" not in str(error):
                raise
        browser.poll(lambda: browser.eval(f"{_visible_elements(INPUT)}.length === 1"), label="商品搜索框")
        entries = [entry for entry in browser.find(INPUT)["entries"] if entry.get("visible", True)]
        if len(entries) != 1:
            raise RuntimeError("商品搜索框不是唯一匹配")
        browser.call("fill", entries[0].get("ref") or INPUT, product_id)
        browser.poll(
            lambda: browser.eval(f"({{value: {_visible_elements(INPUT)}[0]?.value}})").get("value") == product_id,
            label="商品 ID 输入读回",
        )
        buttons = browser.eval(f"{_visible_elements(QUERY)}.map(el => el.innerText.trim())")
        if buttons != ["查询"]:
            raise RuntimeError("未找到唯一的商品查询按钮")
        browser.click(QUERY)
        view = f'[id="{product_id}_view"]'
        browser.poll(lambda: browser.eval(f"{_visible_elements(view)}.length === 1"), label="目标商品查看按钮")
        browser.click(view)
    recovery = "none"
    try:
        snapshot = browser.poll(
            lambda: _ready_snapshot(browser, product_id, groupid),
            label="同商品详情读回", max_seconds=6,
        )
    except TimeoutError:
        pending = browser.eval(DETAIL_SNAPSHOT_JS)
        if pending and is_product_detail_url(pending.get("url", ""), product_id, groupid) and \
                (pending.get("fields") or {}).get("商品名称") and pending.get("detail_text"):
            snapshot = browser.poll(
                lambda: _ready_snapshot(browser, product_id, groupid),
                label="适用门店完成加载", max_seconds=35,
            )
        else:
            detail_url = browser.eval("location.href")
            if not is_product_detail_url(detail_url, product_id, groupid):
                raise RuntimeError("详情正文未加载且商品身份不匹配，拒绝新标签重载")
            try:
                opened = browser.call("tab", "new", detail_url)
                page = opened.get("page") if isinstance(opened, dict) else opened
                if not isinstance(page, (str, int)) or page == "":
                    raise RuntimeError("新标签响应没有 page 标识")
                browser.call("tab", "select", str(page))
            except RuntimeError as error:
                raise RuntimeError("详情正文未加载，新标签重载失败；请在 Chrome 中手动刷新商品详情") from error
            recovery = "fresh_tab"
            snapshot = browser.poll(
                lambda: _ready_snapshot(browser, product_id, groupid),
                label="新标签同商品详情读回", max_seconds=35,
            )
    return {**verify_detail(snapshot, product_id, groupid), "recovery": recovery}


def _ready_snapshot(browser, product_id: str, groupid: str | None = None) -> dict | None:
    snapshot = browser.eval(DETAIL_SNAPSHOT_JS)
    if snapshot and is_product_detail_url(snapshot.get("url", ""), product_id, groupid):
        if _snapshot_complete(snapshot):
            return snapshot
    return None


def _snapshot_complete(snapshot: dict) -> bool:
    if snapshot.get("loading") or not (snapshot.get("fields") or {}).get("商品名称") or not snapshot.get("detail_text"):
        return False
    poi = snapshot.get("poi_readiness")
    if poi is not None:
        expected, displayed = poi.get("expected_count"), poi.get("displayed_count")
        if expected is None or displayed != expected:
            return False
        if displayed > 0 and not poi.get("rows"):
            return False
    return True


def _public_text(value: str) -> str:
    lines = [line.strip() for line in str(value).splitlines()]
    lines = [line for line in lines if line != "--" and not re.search(r"(?:商品\s*ID|采购价|销量|成交券数|成交金额|排名|cookie|二维码)", line, re.I)]
    return re.sub(r"\d{10,}", "", "\n".join(lines)).strip()


def summarize_groups(text: str) -> list[str]:
    headings = []
    for line in text.splitlines():
        match = re.fullmatch(r"([^（()）]{1,24})[（(](\d+\s*选\s*\d+|全部可享)[）)]", line.strip())
        if match:
            choice = re.sub(r"\s+", "", match.group(2))
            headings.append(f"{_public_text(match.group(1))}（{choice}）")
    return headings


def _store_text(value: str) -> str:
    return "\n".join(line for line in _public_text(value).splitlines()
                     if line and line != "门店" and not line.isdigit()
                     and not re.fullmatch(r"已选择\d+家适用门店", line))


def build_product_copy(product: dict, detail: dict, location: str = "") -> dict:
    verify_detail(detail, str(product["product_id"]))
    fields = detail["fields"]
    name = _public_text(fields["商品名称"])
    if not name:
        raise ValueError("商品名称没有可公开的内容")
    location = _public_text(location)
    facts = {key: _public_text(fields[key]) for key in PUBLIC_FIELDS if fields.get(key)}
    if fields.get("售价"):
        price = re.search(r"[¥￥]?\s*(\d[\d,]*(?:\.\d+)?)\s*元?", fields["售价"])
        facts["售价"] = price.group(1) + "元" if price else ""
    if fields.get("适用门店"):
        facts["适用门店"] = _store_text(fields["适用门店"])
        if not location and "已选择1家适用门店" in fields["适用门店"] and facts["适用门店"]:
            location = facts["适用门店"].splitlines()[0]
    groups = summarize_groups(fields.get("组品搭配", ""))
    if groups:
        facts["组品搭配"] = "；".join(groups)
    facts = {key: value for key, value in facts.items() if value}
    caption = "\n".join([name, *[f"{key}：{value}" for key, value in facts.items()]])
    if location:
        caption += f"\n地点：{location}"
    caption += "\n下单前请以商品页面的门店、日期与使用规则为准。"
    public = {
        "title": name[:20], "caption": caption, "location": location, "product_label": name,
    }
    base = (
        "竖版抖音团购图文海报，真实生活方式摄影质感，中文排版清楚。"
        "只使用以下已核实的公开商品信息，完整保留日期与限制，未提供字段省略，不虚构折扣或权益：\n"
        + caption
        + "\n不出现后台商品ID、销量、排名、采购价、二维码或后台截图。"
    )
    prompts = [base + "\n" + direction for direction in (
        "封面突出商品名称和售价，主文案在缩略图中可读。",
        "第二张说明可用日期和使用场景，配图不增添未核实的权益。",
        "第三张清晰展示限购和退款规则，不隐藏限制。",
        "最后一张展示地点和购买前确认规则的提示。",
    )]
    return {"public": public, "image_prompts": prompts}
