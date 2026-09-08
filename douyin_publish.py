#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douyin_publish.py — 抖音团购视频自动发布 CLI（钉钉 AI 表 → 抖音创作者中心 → 回写 AI 表）

链路（全部经 2026-09-08 实战校准）：
  0. 首次使用自动初始化：在钉钉创建 Base「抖音团购发布」+ 数据表「团购视频发布流水线」
     （11 个字段含选项），回读字段 ID 存入 ~/.douyin_publisher.json
  1. dws 读钉钉 AI 表，找 是否需要发布=是 且素材完整的记录
  2. 下载视频/封面（OSS 签名 URL），字节校验
  3. 本地 CORS 服务（127.0.0.1）供页面 fetch —— 抖音页面直接 fetch OSS 会被安全 SDK+CORS 拦
  4. opencli browser 驱动已登录 Chrome：
     DataTransfer 注入视频 → 标题/简介 → 竖封面弹窗 → 位置/带货模式/国内 POI
     → 自主声明(内容由AI生成) → 发布前核对 → 点发布 → 等作品管理页
  5. 轮询作品管理页审核状态（审核中→已发布，默认最长10分钟），过审后经
     creator item list API 取 item_id_plain 拼 PC 链接
  6. dws 回写 素材是否已用=已用、是否需要发布=否、视频链接=PC链接，并回读验证

依赖：PATH 中的 dws（钉钉连接器 CLI，需先 dws 授权钉钉账号）+ opencli（含 Browser Bridge 扩展）
     + Chrome 已登录抖音创作者中心（creator.douyin.com）

用法：
  python3 douyin_publish.py --init        # 首次使用：自动创建钉钉 AI 表并初始化配置
  python3 douyin_publish.py               # 取第一条待发布记录，发布前交互确认
  python3 douyin_publish.py --record-id XX  # 指定记录
  python3 douyin_publish.py --yes         # 跳过发布确认（慎用）
  python3 douyin_publish.py --no-publish  # 只填表不点发布（校准/检查用）
  python3 douyin_publish.py --review-timeout 900  # 审核等待上限秒数（默认600）

注意：
  - dws 回写用 "cells" 键（不是 "fields"），字段 ID 存于 ~/.douyin_publisher.json
  - 抖音发布按钮点击后若触发安全验证，脚本会停止且不回写，需人工处理
  - 换组织/换表：删除 ~/.douyin_publisher.json 重新 --init，或手动编辑其中 baseId/tableId
"""

import argparse
import http.server
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

# ============ 常量 ============
SESSION = "douyin"                                # opencli browser 会话名
UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
MANAGE_PATH = "/creator-micro/content/manage"
LOCAL_PORT = 8765                                 # 本地 CORS 服务端口
CONFIG_PATH = os.path.expanduser("~/.douyin_publisher.json")  # 表配置（首次使用自动生成）

# 以下由配置文件加载（首次使用自动建表时生成），勿硬编码
BASE_ID = None       # AI 表 Base ID
TABLE_ID = None      # 数据表 ID
OPT_PUBLISH_YES = None  # 「是否需要发布」=是 的选项 ID

# 表结构 Schema（首次使用按此创建）
TABLE_SCHEMA = [
    {"fieldName": "推广门店位置", "type": "multipleSelect",
     "config": {"options": [{"name": "示例门店（请改为你自己的门店）"}]}},
    {"fieldName": "提示词", "type": "text"},
    {"fieldName": "视觉参考", "type": "attachment"},
    {"fieldName": "视频输出", "type": "attachment"},
    {"fieldName": "素材是否已用", "type": "singleSelect",
     "config": {"options": [{"name": "已用"}, {"name": "未用"}, {"name": "不用"}]}},
    {"fieldName": "发布账户", "type": "singleSelect",
     "config": {"options": [{"name": "示例账号（请改为你的抖音号名）"}]}},
    {"fieldName": "作品标题", "type": "text"},
    {"fieldName": "作品简介", "type": "text"},
    {"fieldName": "是否需要发布", "type": "singleSelect",
     "config": {"options": [{"name": "是"}, {"name": "否"}]}},
    {"fieldName": "视频封面", "type": "attachment"},
    {"fieldName": "视频链接", "type": "url"},
]

# AI 表字段 ID（由配置加载/建表后回读填充）
FIELD_MAP = {}
ITEM_LIST_API = "https://creator.douyin.com/aweme/v1/creator/item/list/?page=0&count=10"


def log(stage, msg):
    print("[%s] %s" % (stage, msg), flush=True)


# ============ 工具：子进程 ============
def run(cmd, timeout=120, input_text=None):
    """运行外部命令，返回 (returncode, stdout+stderr)"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, input=input_text)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after %ss: %s" % (timeout, " ".join(cmd[:6]))


def dws(*args, timeout=120):
    """调 dws，解析 JSON 输出（从输出中提取最后一个 JSON 对象）"""
    cmd = ["dws"] + list(args) + ["--format", "json"]
    code, out = run(cmd, timeout=timeout)
    # 输出可能带 retry 行，找第一个 '{' 起的 JSON
    start = out.find("{")
    if start < 0:
        raise RuntimeError("dws 无 JSON 输出(rc=%s): %s" % (code, out[:300]))
    return json.loads(out[start:])


def opencli(*args, timeout=120):
    cmd = ["opencli", "browser", SESSION] + list(args)
    code, out = run(cmd, timeout=timeout)
    return out


# ============ 配置与首次建表 ============
def _apply_config(cfg):
    """把配置字典填充到模块级常量"""
    global BASE_ID, TABLE_ID, OPT_PUBLISH_YES
    BASE_ID = cfg["baseId"]
    TABLE_ID = cfg["tableId"]
    OPT_PUBLISH_YES = cfg["optPublishYes"]
    FIELD_MAP.clear()
    FIELD_MAP.update(cfg["fields"])


def _create_table_interactive():
    """首次使用：在钉钉里新建 Base「抖音团购发布」+ 数据表，回读字段 ID 后存配置"""
    print("\n未找到配置文件 %s" % CONFIG_PATH)
    print("将在你的钉钉组织中自动创建：")
    print("  · Base「抖音团购发布」（含数据表「团购视频发布流水线」，11 个字段）")
    print("  · 其中「示例门店」「示例账号」两个选项请建好后自行改为你的真实门店/账号")
    answer = input("确认创建？[y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        raise RuntimeError("用户取消初始化。已有现成表？可手动建 ~/.douyin_publisher.json："
                           '{"baseId":"...","tableId":"...","optPublishYes":"...","fields":{...}}')

    log("建表", "创建 Base「抖音团购发布」...")
    resp = dws("aitable", "base", "create", "--name", "抖音团购发布", timeout=120)
    base_id = (resp.get("data") or {}).get("baseId") or resp.get("baseId")
    if not base_id:
        raise RuntimeError("Base 创建失败：%s" % json.dumps(resp, ensure_ascii=False)[:300])
    log("建表", "Base 创建成功: %s" % base_id)

    log("建表", "创建数据表「团购视频发布流水线」（11 个字段）...")
    resp = dws("aitable", "table", "create",
               "--base-id", base_id, "--name", "团购视频发布流水线",
               "--fields", json.dumps(TABLE_SCHEMA, ensure_ascii=False), timeout=120)
    table_id = (resp.get("data") or {}).get("tableId") or resp.get("tableId")
    if not table_id:
        raise RuntimeError("数据表创建失败：%s" % json.dumps(resp, ensure_ascii=False)[:300])
    log("建表", "数据表创建成功: %s" % table_id)

    # 回读字段 ID 和「是」选项 ID
    log("建表", "回读字段 ID ...")
    resp = dws("aitable", "field", "list",
               "--base-id", base_id, "--table-id", table_id, timeout=60)
    fields = (resp.get("data") or {}).get("fields", [])
    cfg_fields = {}
    opt_yes = None
    for f in fields:
        name, fid = f.get("fieldName"), f.get("fieldId")
        if name in [s["fieldName"] for s in TABLE_SCHEMA]:
            cfg_fields[name] = fid
        if name == "是否需要发布":
            for opt in ((f.get("config") or {}).get("options") or []):
                if opt.get("name") == "是":
                    opt_yes = opt.get("id")
    missing = [s["fieldName"] for s in TABLE_SCHEMA if s["fieldName"] not in cfg_fields]
    if missing or not opt_yes:
        raise RuntimeError("字段回读不完整（缺 %s，opt_yes=%s），请手动检查 Base %s 的表 %s"
                           % (missing, opt_yes, base_id, table_id))

    cfg = {
        "baseId": base_id, "tableId": table_id, "optPublishYes": opt_yes,
        "fields": cfg_fields,
        "docUrl": "https://alidocs.dingtalk.com/i/nodes/%s" % base_id,
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    _apply_config(cfg)
    log("建表", "配置已保存到 %s" % CONFIG_PATH)
    log("建表", "表格链接: %s" % cfg["docUrl"])
    log("建表", "下一步：打开表格把「示例门店」「示例账号」改成真实值，添加待发布记录后重跑本脚本")


def load_config(auto_init=True):
    """加载表配置；不存在且 auto_init 时引导首次建表"""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        for key in ("baseId", "tableId", "optPublishYes", "fields"):
            if key not in cfg:
                raise RuntimeError("配置文件 %s 缺少 %s，请补全或删除后重新初始化" % (CONFIG_PATH, key))
        _apply_config(cfg)
        log("配置", "已加载 %s（Base %s 表 %s）" % (CONFIG_PATH, BASE_ID, TABLE_ID))
        return
    if auto_init:
        _create_table_interactive()
    else:
        raise RuntimeError("未找到配置 %s；先运行一次本脚本完成初始化" % CONFIG_PATH)


def oc_eval(js, timeout=120):
    """opencli eval，解析返回 JSON"""
    out = opencli("eval", js, timeout=timeout)
    start = out.find("{")
    if start < 0:
        raise RuntimeError("eval 无 JSON 输出: %s" % out[:300])
    return json.loads(out[start:])


# ============ 步骤 1：读 AI 表 ============
def option_name(value):
    """单选/多选字段取显示名"""
    if isinstance(value, list):
        return option_name(value[0]) if value else ""
    if isinstance(value, dict):
        return str(value.get("name") or value.get("text") or "")
    return "" if value is None else str(value)


def first_attachment(value):
    """附件字段取第一个元素"""
    if isinstance(value, list) and value:
        return value[0]
    return None


def find_pending_record(record_id=None):
    log("读表", "查询 是否需要发布=%s 的记录..." % ("指定ID" if record_id else "是"))
    if record_id:
        resp = dws("aitable", "record", "query",
                   "--base-id", BASE_ID, "--table-id", TABLE_ID,
                   "--record-ids", record_id)
        records = resp.get("data", {}).get("records", [])
        if not records:
            raise RuntimeError("记录 %s 不存在" % record_id)
        rec = records[0]
        if option_name(rec["cells"].get(FIELD_MAP["是否需要发布"])) != "是":
            raise RuntimeError("记录 %s 的 是否需要发布 不是「是」，拒绝发布" % record_id)
    else:
        resp = dws("aitable", "record", "query",
                   "--base-id", BASE_ID, "--table-id", TABLE_ID,
                   "--filters", json.dumps({
                       "operator": "and",
                       "operands": [{"operator": "eq",
                                     "operands": [FIELD_MAP["是否需要发布"], OPT_PUBLISH_YES]}]}),
                   "--limit", "20")
        records = resp.get("data", {}).get("records", [])

    for rec in records:
        cells = rec.get("cells", {})
        video = first_attachment(cells.get(FIELD_MAP["视频输出"]))
        cover = first_attachment(cells.get(FIELD_MAP["视频封面"]))
        title = str(cells.get(FIELD_MAP["作品标题"]) or "").strip()
        desc = str(cells.get(FIELD_MAP["作品简介"]) or "").strip()
        poi = option_name(cells.get(FIELD_MAP["推广门店位置"])).strip()
        missing = []
        if not title: missing.append("作品标题")
        if not desc: missing.append("作品简介")
        if not poi: missing.append("推广门店位置")
        if not video: missing.append("视频输出")
        if not cover: missing.append("视频封面")
        if missing:
            log("读表", "跳过记录 %s（缺 %s）" % (rec.get("recordId"), "、".join(missing)))
            continue
        return {
            "recordId": rec["recordId"],
            "title": title[:30],           # 抖音标题上限 30 字（同 Automa V8）
            "description": desc,
            "poi": poi,
            "videoUrl": video["url"], "videoName": video.get("filename") or "douyin-video.mp4",
            "videoSize": video.get("size") or 0,
            "coverUrl": cover["url"], "coverName": cover.get("filename") or "douyin-cover.png",
            "coverSize": cover.get("size") or 0,
        }
    raise RuntimeError("没有待发布且素材完整的记录" if not records else "待发布记录均缺素材")


# ============ 步骤 2：下载素材 + 本地 CORS 服务 ============
def download(url, path, expected_size, label):
    log("下载", "%s: %s" % (label, url.split("?")[0].split("/")[-1]))
    urllib.request.urlretrieve(url, path)
    size = os.path.getsize(path)
    if expected_size and size != expected_size:
        raise RuntimeError("%s 字节校验失败：期望 %d 实际 %d" % (label, expected_size, size))
    with open(path, "rb") as f:
        head = f.read(12)
    if label == "封面" and not head.startswith(b"\x89PNG") and not head[:3] == b"\xff\xd8\xff":
        raise RuntimeError("封面文件头不是 PNG/JPEG")
    log("下载", "%s 完成（%d 字节，校验通过）" % (label, size))


class CorsServer(threading.Thread):
    """本地 CORS 文件服务：页面 fetch 本地文件绕开抖音安全 SDK + OSS CORS 限制"""
    def __init__(self, directory, port):
        super().__init__(daemon=True)
        self.directory, self.port = directory, port
        self.httpd = None

    def run(self):
        os.chdir(self.directory)
        handler = type("CORSHandler", (http.server.SimpleHTTPRequestHandler,), {
            "end_headers": lambda self: (self.send_header("Access-Control-Allow-Origin", "*"),
                                         http.server.SimpleHTTPRequestHandler.end_headers(self)),
            "log_message": lambda self, *a: None,
        })
        socketserver.TCPServer.allow_reuse_address = True
        self.httpd = socketserver.TCPServer(("127.0.0.1", self.port), handler)
        self.httpd.serve_forever()

    def stop(self):
        if self.httpd:
            threading.Thread(target=self.httpd.shutdown, daemon=True).start()


# ============ 步骤 3-8：抖音发布流程 ============
JS_BASE = """
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const visible = (el) => Boolean(el && el.getClientRects().length);
const normalize = (v) => String(v || '').replace(/[\\u200b-\\u200d\\ufeff]/g, '').replace(/\\s+/g, ' ').trim();
const exact = (text, root) => [...(root||document).querySelectorAll('div,span,li,button,label')]
  .filter(el => visible(el) && el.textContent.trim() === text);
"""


def step_open_upload():
    log("抖音", "打开发布页...")
    opencli("open", UPLOAD_URL, "--window", "foreground", timeout=90)
    time.sleep(6)
    out = oc_eval("(() => { const b = document.body.innerText || ''; const u = location.href;"
                  " return JSON.stringify({ok: /creator\\.douyin\\.com/.test(u) && !/扫码登录|验证码登录/.test(b)}); })()")
    if not out.get("ok"):
        raise RuntimeError("抖音创作者中心未登录，请先登录后重跑")


def step_inject_video(record):
    log("抖音", "注入视频 %s（%d 字节）..." % (record["videoName"], record["videoSize"]))
    js = """(async () => {
  %s
  let input = null;
  for (let i = 0; i < 30 && !input; i++) {
    const inputs = [...document.querySelectorAll('input[type="file"]')];
    input = inputs.find(el => /video|mp4/i.test(String(el.accept || ''))) || inputs[0] || null;
    if (!input) await sleep(350);
  }
  if (!input) return JSON.stringify({error: '未找到视频上传控件'});
  const resp = await fetch('http://127.0.0.1:%d/video.mp4');
  if (!resp.ok) return JSON.stringify({error: '本地取视频失败 ' + resp.status});
  const blob = await resp.blob();
  const file = new File([blob], 'douyin-video.mp4', {type: blob.type || 'video/mp4'});
  const dt = new DataTransfer();
  dt.items.add(file);
  input.files = dt.files;
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
  // 等上传完成（出现重新上传）
  const started = Date.now();
  while (Date.now() - started < 180000) {
    const body = document.body.innerText || '';
    if (/上传失败|上传出错|重新选择文件/.test(body)) return JSON.stringify({error: '视频上传失败'});
    if (/重新上传/.test(body)) return JSON.stringify({uploadReady: true, bytes: blob.size});
    await sleep(1500);
  }
  return JSON.stringify({error: '等待视频上传完成超时'});
})()""" % (JS_BASE, LOCAL_PORT)
    out = oc_eval(js, timeout=240)
    if out.get("error"):
        raise RuntimeError("视频上传失败：%s" % out["error"])
    log("抖音", "视频上传完成（%d 字节）" % out.get("bytes", 0))


def step_fill_title_desc(record):
    log("抖音", "填写标题/简介...")
    r1 = opencli("type", 'input[placeholder*="作品标题"], input[placeholder*="填写标题"], input[placeholder*="添加标题"]',
                 record["title"], timeout=60)
    if '"typed": true' not in r1 and '"typed":true' not in r1:
        raise RuntimeError("标题填写失败: %s" % r1[:200])
    r2 = opencli("click", 'div.zone-container[contenteditable="true"]', timeout=60)
    r3 = opencli("type", 'div.zone-container[contenteditable="true"]', record["description"], timeout=60)
    if '"typed": true' not in r3 and '"typed":true' not in r3:
        raise RuntimeError("简介填写失败: %s" % r3[:200])


def step_upload_cover(record):
    log("抖音", "上传竖封面...")
    js = """(async () => {
  %s
  try {
    const entry = exact('选择封面', document)[0];
    if (!entry) return JSON.stringify({error: '未找到选择封面入口'});
    entry.click(); await sleep(1000);
    let modal = null;
    for (let i = 0; i < 20 && !modal; i++) { modal = [...document.querySelectorAll('div.dy-creator-content-modal')].find(visible); if (!modal) await sleep(300); }
    if (!modal) return JSON.stringify({error: '封面弹窗未打开'});
    let portrait = null;
    for (let i = 0; i < 20 && !portrait; i++) { portrait = [...modal.querySelectorAll('.steps-cgzd9T .step-dXVbPX')].find(el => visible(el) && el.textContent.trim() === '设置竖封面'); if (!portrait) await sleep(300); }
    if (!portrait) return JSON.stringify({error: '未找到竖封面切换项'});
    portrait.click();
    for (let i = 0; i < 20 && !portrait.className.includes('step-active'); i++) await sleep(300);
    await sleep(800);
    // 第二个 input 才是上传框（第一个是 AI 参考图）
    const input = modal.querySelectorAll('input.semi-upload-hidden-input')[1];
    if (!input) return JSON.stringify({error: '未找到封面上传框'});
    const resp = await fetch('http://127.0.0.1:%d/cover.png');
    const blob = await resp.blob();
    const dt = new DataTransfer();
    dt.items.add(new File([blob], 'douyin-cover.png', {type: blob.type || 'image/png'}));
    input.files = dt.files;
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
    await sleep(2500);
    let done = null;
    for (let i = 0; i < 20 && !done; i++) { done = [...modal.querySelectorAll('button')].find(el => visible(el) && el.innerText.trim() === '完成' && !el.disabled); if (!done) await sleep(500); }
    if (!done) return JSON.stringify({error: '封面完成按钮不可用'});
    done.click();
    for (let i = 0; i < 40; i++) {
      const skip = exact('暂不设置', document)[0];
      if (skip) { skip.click(); await sleep(600); }
      if (!visible(modal)) return JSON.stringify({coverApplied: true, bytes: blob.size});
      await sleep(500);
    }
    return JSON.stringify({error: '封面弹窗没有关闭'});
  } catch (e) { return JSON.stringify({error: e.message}); }
})()""" % (JS_BASE, LOCAL_PORT)
    out = oc_eval(js, timeout=180)
    if out.get("error"):
        raise RuntimeError("封面上传失败：%s" % out["error"])
    log("抖音", "封面应用完成（%d 字节）" % out.get("bytes", 0))


def step_select_poi(record):
    log("抖音", "选择位置/带货模式/POI: %s" % record["poi"])
    js = """(async () => {
  %s
  const xpathOne = (path) => document.evaluate(path, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
  const clickLast = (items) => { const el = items[items.length - 1]; if (!el) return false; el.click(); return true; };
  const chooseSelect = async (index, value, optional) => {
    const select = xpathOne('(//*[normalize-space(.)="添加标签"]/following::*[contains(concat(" ",normalize-space(@class)," ")," semi-select ")])[' + index + ']');
    if (!visible(select)) { if (optional) return false; throw new Error('未找到添加标签的第' + index + '个选择框'); }
    if ((select.innerText || '').includes(value)) return true;
    select.click(); await sleep(400);
    const options = exact(value).filter(el => el.closest('.semi-portal,.semi-popover,.semi-dropdown,[role="listbox"]'));
    if (!clickLast(options)) { if (optional) return false; throw new Error('未找到下拉选项:' + value); }
    await sleep(500); return true;
  };
  try {
    document.querySelectorAll('.shepherd-element,.shepherd-modal-overlay-container').forEach(el => el.remove());
    await chooseSelect(1, '位置');
    await chooseSelect(2, '带货模式', true);
    const target = %s;
    const trigger = [...document.querySelectorAll('[id="douyin_creator_pc_anchor_jump"] .semi-select-selection-text')]
      .find(el => visible(el) && el.textContent.trim() === '输入地理位置');
    if (!trigger) return JSON.stringify({error: '未找到位置入口'});
    trigger.closest('.semi-select').setAttribute('data-poi', 'true');
    trigger.click(); await sleep(600);
    let domestic = null;
    for (let i = 0; i < 20 && !domestic; i++) { domestic = [...document.querySelectorAll('#domestic')].find(visible); if (!domestic) await sleep(300); }
    if (!domestic) return JSON.stringify({error: '未找到国内选项 #domestic'});
    domestic.click(); await sleep(500);
    const dropdown = domestic.closest('.semi-popover');
    if (!dropdown) return JSON.stringify({error: '未找到国内位置候选弹层'});
    const input = [...document.querySelectorAll('[data-poi="true"] .semi-select-input > input.semi-input')].find(visible);
    if (!input) return JSON.stringify({error: '未找到地理位置输入框'});
    input.focus();
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, ''); input.dispatchEvent(new Event('input', {bubbles: true}));
    setter.call(input, target); input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
    await sleep(2500);
    let firstName = '', selected = null;
    for (let i = 0; i < 40 && !selected; i++) {
      const first = [...dropdown.querySelectorAll('[role="listbox"] [role="option"]')].find(visible);
      if (first) {
        firstName = normalize(first.innerText.trim().split('\\n')[0].split('商场内有多家门店')[0]);
        if (firstName === normalize(target)) selected = first;
      }
      if (!selected) await sleep(300);
    }
    if (!selected) return JSON.stringify({error: 'POI 首项与目标不一致', firstName: firstName, target: target});
    const selectedText = selected.innerText.trim();
    selected.click(); await sleep(1000);
    return JSON.stringify({poiSelected: selectedText.slice(0, 80)});
  } catch (e) { return JSON.stringify({error: e.message}); }
})()""" % (JS_BASE, json.dumps(record["poi"], ensure_ascii=False))
    out = oc_eval(js, timeout=120)
    if out.get("error"):
        raise RuntimeError("POI 选择失败：%s（首项=%s）" % (out["error"], out.get("firstName", "")))
    log("抖音", "POI 已选中: %s" % out.get("poiSelected", ""))


def step_ai_declaration():
    log("抖音", "设置自主声明（内容由AI生成）...")
    js = """(async () => {
  %s
  try {
    const entries = [...exact('添加声明', document), ...exact('请选择自主声明', document)].filter(Boolean);
    const entry = entries[entries.length - 1];
    if (entry) { entry.click(); await sleep(600); }
    // 可见的声明弹窗
    const modal = [...document.querySelectorAll('[role="dialog"], .semi-modal, div[class*="modal"]')].find(visible);
    if (!modal) return JSON.stringify({error: '声明弹窗未打开'});
    const span = [...modal.querySelectorAll('span.semi-radio-addon')].find(el => visible(el) && el.textContent.trim() === '内容由AI生成');
    if (!span) return JSON.stringify({error: '弹窗内未找到 内容由AI生成 选项'});
    const label = span.closest('label.semi-radio');
    if (!label) return JSON.stringify({error: '未找到 label.semi-radio'});
    label.click(); await sleep(700);
    const nowChecked = label.className.includes('checked') || (label.querySelector('input') && label.querySelector('input').checked);
    if (!nowChecked) return JSON.stringify({error: '点击后仍未选中'});
    const confirmBtn = [...modal.querySelectorAll('button')].find(b => visible(b) && b.textContent.trim() === '确定' && !b.disabled);
    if (!confirmBtn) return JSON.stringify({error: '确定按钮不可用'});
    confirmBtn.click(); await sleep(800);
    const stillOpen = [...document.querySelectorAll('[role="dialog"], .semi-modal, div[class*="modal"]')].find(visible);
    return JSON.stringify({declared: !stillOpen});
  } catch (e) { return JSON.stringify({error: e.message}); }
})()""" % JS_BASE
    out = oc_eval(js, timeout=60)
    if out.get("error"):
        raise RuntimeError("AI 声明设置失败：%s" % out["error"])


def step_verify(record):
    log("核对", "发布前核对全部字段...")
    js = """(() => {
  const normalize = (v) => String(v || '').replace(/[\\u200b-\\u200d\\ufeff]/g, '').replace(/\\s+/g, ' ').trim();
  const title = document.querySelector('input[placeholder*="作品标题"], input[placeholder*="填写标题"], input[placeholder*="添加标题"]')?.value || '';
  const description = document.querySelector('div.zone-container[contenteditable="true"]')?.innerText || '';
  const poi = document.querySelector('[data-poi="true"] .semi-select-selection-text')?.innerText || '';
  const body = document.body.innerText || '';
  const errors = [];
  if (normalize(title) !== %s) errors.push('标题不符:' + title);
  if (!normalize(description).includes(%s)) errors.push('简介不符');
  if (!normalize(poi).includes(%s)) errors.push('位置未选中:' + normalize(poi).slice(0, 20));
  if (!/重新上传/.test(body)) errors.push('视频未确认');
  if (![...document.querySelectorAll('div,span')].some(el => el.getClientRects().length && el.textContent.trim() === '内容由AI生成')) errors.push('AI声明未在页面显示');
  const publishBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '发布' && !b.disabled);
  return JSON.stringify({ok: errors.length === 0, errors: errors, publishReady: Boolean(publishBtn)});
})()""" % (json.dumps(record["title"], ensure_ascii=False),
           json.dumps(record["description"][:15], ensure_ascii=False),
           json.dumps(record["poi"], ensure_ascii=False))
    out = oc_eval(js, timeout=30)
    if not out.get("ok"):
        raise RuntimeError("发布前核对未通过：%s" % "；".join(out.get("errors", [])))
    if not out.get("publishReady"):
        raise RuntimeError("发布按钮不可用")
    log("核对", "全部通过 ✓")


def step_publish():
    js = """(() => {
  const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '发布' && !b.disabled);
  if (!btn) return JSON.stringify({error: '发布按钮不可用'});
  btn.click(); return JSON.stringify({clicked: true});
})()"""
    out = oc_eval(js, timeout=30)
    if out.get("error"):
        raise RuntimeError("点击发布失败：%s" % out["error"])
    log("发布", "已点击发布，等待作品管理页确认...")
    js_wait = """(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const started = Date.now();
  while (Date.now() - started < 120000) {
    const body = document.body.innerText || '';
    if (/发布失败|发布出错|投稿失败/.test(body)) return JSON.stringify({error: '抖音返回发布失败'});
    if (/获取验证码|短信验证|安全验证|请完成验证/.test(body)) return JSON.stringify({error: '触发安全验证，需人工处理'});
    if (location.hostname === 'creator.douyin.com' && location.pathname.indexOf('%s') === 0) {
      return JSON.stringify({published: true, url: location.href});
    }
    await sleep(1500);
  }
  return JSON.stringify({error: '确认超时：未进入作品管理页。请先人工核对是否已投稿，勿重跑以免重复发布', url: location.href});
})()""" % MANAGE_PATH
    out = oc_eval(js_wait, timeout=150)
    if out.get("error"):
        raise RuntimeError(out["error"])
    log("发布", "发布成功 ✓ 已进入作品管理页")


# ============ 步骤 8.5：等待平台审核并取视频链接 ============
def _check_review_status(title):
    """在作品管理页查指定标题作品的状态。返回 '已发布'/'审核中'/'未通过'/None"""
    js = """(() => {
  const visible = (el) => Boolean(el && el.getClientRects().length);
  const title = %s;
  // 含标题的容器（自内向外找到第一个同时含状态标签的行容器）
  const cands = [...document.querySelectorAll('div,li')].filter(el => visible(el) && (el.innerText||'').includes(title));
  for (let i = cands.length - 1; i >= 0; i--) {
    const tags = [...cands[i].querySelectorAll('div,span')].filter(el => visible(el) && !el.children.length);
    const m = tags.map(el => el.textContent.trim()).find(t => t === '已发布' || t === '审核中' || t === '未通过');
    if (m) return JSON.stringify({status: m});
  }
  return JSON.stringify({status: null});
})()""" % json.dumps(title[:20], ensure_ascii=False)
    try:
        out = oc_eval(js, timeout=30)
        return out.get("status")
    except Exception:
        return None


def _fetch_item_link(title):
    """经 creator item list API 匹配标题取 item_id_plain，拼 PC 链接"""
    js = """(async () => {
  try {
    const resp = await fetch('%s', {credentials: 'include'});
    if (!resp.ok) return JSON.stringify({error: 'api ' + resp.status});
    const data = await resp.json();
    const title = %s;
    const it = (data.item_info_list || []).find(x => (x.title || '').includes(title));
    if (!it || !it.item_id_plain) return JSON.stringify({error: '作品列表中未找到该标题'});
    return JSON.stringify({itemId: it.item_id_plain, createTime: it.create_time});
  } catch (e) { return JSON.stringify({error: e.message}); }
})()""" % (ITEM_LIST_API, json.dumps(title[:20], ensure_ascii=False))
    out = oc_eval(js, timeout=30)
    if out.get("error"):
        raise RuntimeError("取视频链接失败：%s" % out["error"])
    return "https://www.douyin.com/video/%s" % out["itemId"]


def step_wait_review_get_link(record, timeout_sec=600):
    """发布后等平台审核（审核中→已发布），过审后取 PC 链接。
    注意：当前页必须在作品管理页（step_publish 结束时已跳转）。"""
    log("审核", "等待平台审核（每 30s 查一次，最长 %d 分钟）..." % (timeout_sec // 60))
    started = time.time()
    first = True
    while time.time() - started < timeout_sec:
        if not first:
            # 刷新作品管理页，避免 DOM 状态过期（首次进入时页面刚加载，无需刷新）
            opencli("open", "https://creator.douyin.com" + MANAGE_PATH, timeout=60)
            time.sleep(5)
        first = False
        status = _check_review_status(record["title"])
        if status == "已发布":
            log("审核", "已过审（等待 %d 秒）✓" % int(time.time() - started))
            link = _fetch_item_link(record["title"])
            log("审核", "视频链接: %s" % link)
            return link
        if status == "未通过":
            raise RuntimeError("作品审核未通过！不回写链接，请到作品管理页查看原因")
        # status 为 '审核中' 或 None（页面可能未加载完/会话断开），继续等
        if status:
            log("审核", "当前状态: %s，继续等待..." % status)
        time.sleep(30)
    # 超时：视频通常已发布只是状态没刷出来，尽力取一次链接
    log("审核", "等待超时（%d 分钟）。最后尝试取一次链接..." % (timeout_sec // 60))
    try:
        link = _fetch_item_link(record["title"])
        log("审核", "链接已取到（请人工确认审核状态）: %s" % link)
        return link
    except RuntimeError as e:
        raise RuntimeError("审核等待超时且取链接失败：%s（请人工到作品管理页确认后手动回填）" % e)


# ============ 步骤 9：回写 AI 表 ============
def step_write_back(record_id, link=None):
    log("回写", "素材是否已用=已用、是否需要发布=否%s ..." % ("、视频链接=PC链接" if link else ""))
    cells = {FIELD_MAP["素材是否已用"]: "已用",
             FIELD_MAP["是否需要发布"]: "否"}
    if link:
        cells[FIELD_MAP["视频链接"]] = link
    payload = json.dumps([{"recordId": record_id, "cells": cells}], ensure_ascii=False)
    last_err = None
    for attempt in range(3):
        try:
            resp = dws("aitable", "record", "update",
                       "--base-id", BASE_ID, "--table-id", TABLE_ID,
                       "--records", payload, timeout=120)
            if resp.get("success") or resp.get("data", {}).get("recordIds"):
                break
            last_err = json.dumps(resp.get("error", {}), ensure_ascii=False)[:200]
        except Exception as e:
            last_err = str(e)[:200]
        log("回写", "第 %d 次失败（%s），重试..." % (attempt + 1, last_err))
        time.sleep(3)
    else:
        raise RuntimeError("回写失败（发布已完成，请手动改表！）：%s" % last_err)

    # 回读验证
    resp = dws("aitable", "record", "query",
               "--base-id", BASE_ID, "--table-id", TABLE_ID,
               "--record-ids", record_id, timeout=60)
    records = resp.get("data", {}).get("records", [])
    if records:
        cells = records[0].get("cells", {})
        used = option_name(cells.get(FIELD_MAP["素材是否已用"]))
        req = option_name(cells.get(FIELD_MAP["是否需要发布"]))
        if used != "已用" or req != "否":
            raise RuntimeError("回读校验失败：素材是否已用=%s、是否需要发布=%s（请手动核对）" % (used, req))
        if link:
            read_link = cells.get(FIELD_MAP["视频链接"]) or {}
            actual = read_link.get("link") if isinstance(read_link, dict) else read_link
            if actual != link:
                raise RuntimeError("回读校验失败：视频链接=%s（期望 %s，请手动核对）" % (actual, link))
    log("回写", "回读验证通过 ✓")


# ============ 主流程 ============
def main():
    ap = argparse.ArgumentParser(description="抖音团购视频自动发布（钉钉AI表 → 抖音 → 回写）")
    ap.add_argument("--record-id", help="指定记录 ID（默认取第一条 待发布=是 且素材完整的记录）")
    ap.add_argument("--yes", action="store_true", help="跳过发布前交互确认（慎用）")
    ap.add_argument("--no-publish", action="store_true", help="填完表单停在发布前，不点发布（校准用）")
    ap.add_argument("--review-timeout", type=int, default=600, metavar="SEC",
                    help="发布后等待平台审核的上限秒数（默认 600=10分钟）")
    ap.add_argument("--init", action="store_true",
                    help="仅初始化：首次使用自动创建钉钉 AI 表（Base+表+字段）后退出")
    args = ap.parse_args()

    # 0. 加载表配置（首次使用自动引导建表）
    load_config(auto_init=True)
    if args.init:
        log("完成", "初始化完成，可打开表格添加待发布记录后重跑本脚本")
        return

    # 1. 读表
    record = find_pending_record(args.record_id)
    log("读表", "选中记录 %s：%s（POI: %s）" % (record["recordId"], record["title"], record["poi"]))

    # 2. 下载素材
    tmpdir = tempfile.mkdtemp(prefix="douyin-publish-")
    download(record["videoUrl"], os.path.join(tmpdir, "video.mp4"), record["videoSize"], "视频")
    download(record["coverUrl"], os.path.join(tmpdir, "cover.png"), record["coverSize"], "封面")

    # 3. 本地 CORS 服务
    server = CorsServer(tmpdir, LOCAL_PORT)
    server.start()
    time.sleep(1)
    log("服务", "本地 CORS 服务 http://127.0.0.1:%d 已启动" % LOCAL_PORT)

    try:
        # 4-8. 发布流程
        step_open_upload()
        step_inject_video(record)
        step_fill_title_desc(record)
        step_upload_cover(record)
        step_select_poi(record)
        step_ai_declaration()
        step_verify(record)

        if args.no_publish:
            log("完成", "--no-publish 模式：表单已填好并核对通过，未点发布、未回写。浏览器中可人工检查。")
            return

        if not args.yes:
            answer = input("\n即将真实发布到当前登录的抖音账号（记录 %s，%s）。确认发布？[y/N] "
                           % (record["recordId"], record["title"]))
            if answer.strip().lower() not in ("y", "yes"):
                log("完成", "用户取消发布。表单保持原样，未回写。")
                return

        step_publish()
        link = step_wait_review_get_link(record, timeout_sec=args.review_timeout)
        step_write_back(record["recordId"], link)
        log("完成", "记录 %s 发布并回写成功 ✓（链接: %s）" % (record["recordId"], link))
    finally:
        server.stop()
        log("服务", "本地服务已停止（临时目录: %s）" % tmpdir)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print("\n✖ 失败：%s" % e, file=sys.stderr)
        sys.exit(1)
