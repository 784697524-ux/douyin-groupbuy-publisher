#!/usr/bin/env python3
"""Publish validated videos in one persistent Douyin browser session."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path


CREATOR_HOME = "https://creator.douyin.com/creator-micro/home"
UPLOAD = "https://creator.douyin.com/creator-micro/content/upload"
WORK_LIST = "https://creator.douyin.com/janus/douyin/creator/pc/work_list?status=0&count=12&max_cursor=0&scene=star_atlas&device_platform=android&aid=1128"
STOP_STAGES = {"submission_uncertain", "submitted_unverified", "prepare_failed"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm", ".flv", ".wmv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_manifest(path: Path, account: str, expected_account: str) -> list[dict]:
    path = path.expanduser().resolve()
    items = read(path).get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("manifest 必须包含非空 items 数组")
    if not account or Path(account).name != account or account in (".", "..") or not expected_account.strip():
        raise ValueError("account 必须为单个名称，expected-account 必须非空")
    jobs, ids = [], set()
    for item in items:
        job = dict(item)
        identifier = str(job.get("id", ""))
        if not identifier or identifier in (".", "..") or Path(identifier).name != identifier or identifier in ids:
            raise ValueError("每条 id 必须是唯一的单个文件夹名")
        ids.add(identifier)
        job["id"] = identifier
        for field in ("title", "desc", "location"):
            if not isinstance(job.get(field), str) or not job[field].strip():
                raise ValueError(f"每条任务必须有非空 {field}")
        if len(job["title"]) > 30:
            raise ValueError("title 不得超过平台的 30 字限制")
        tags = job.get("tags", [])
        job["tags"] = [tag.strip() for tag in tags.split(",") if tag.strip()] if isinstance(tags, str) else tags
        if not isinstance(job["tags"], list) or any(not isinstance(tag, str) for tag in job["tags"]):
            raise ValueError("tags 必须是文本数组或逗号分隔文本")
        for field in ("file", "cover"):
            if not job.get(field):
                raise ValueError(f"每条任务必须提供 {field}")
            asset = Path(job[field]).expanduser()
            asset = (path.parent / asset).resolve() if not asset.is_absolute() else asset.resolve()
            if not asset.is_file() or not asset.stat().st_size:
                raise ValueError(f"{field} 必须是存在的非空文件")
            allowed = VIDEO_EXTENSIONS if field == "file" else IMAGE_EXTENSIONS
            if asset.suffix.lower() not in allowed:
                raise ValueError(f"{field} 文件扩展名不受发布运行时支持")
            job[field], job[field + "_sha256"] = str(asset), sha256(asset)
        if job.get("source_sha256") and job["source_sha256"].lower() != job["file_sha256"]:
            raise ValueError("source_sha256 与待上传视频不一致")
        excluded = job.get("exclude_aweme_ids", [])
        if not isinstance(excluded, list):
            raise ValueError("exclude_aweme_ids 必须是数组")
        job["exclude_aweme_ids"] = [str(value) for value in excluded]
        content = {key: job[key] for key in ("id", "title", "desc", "tags", "location", "file_sha256", "cover_sha256")}
        content.update(account=account, expected_account=expected_account, declaration="AI")
        job["fingerprint"] = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        jobs.append(job)
    return jobs


def pending_jobs(jobs: list[dict], state_dir: Path) -> list[dict]:
    pending = []
    for job in jobs:
        state = read(state_dir / job["id"] / "state.json")
        if state and state.get("fingerprint") != job["fingerprint"]:
            raise RuntimeError("已有状态的内容 fingerprint 不一致，拒绝复用")
        stage = state.get("stage")
        if stage in STOP_STAGES:
            raise RuntimeError("已有不确定提交、未核验回执或准备失败记录；停止续跑")
        if stage == "submitted":
            if not state.get("receipt", {}).get("matched") or not state["receipt"].get("aweme_id"):
                raise RuntimeError("submitted 状态缺少可核验作品编号")
            continue
        pending.append(job)
    return pending


async def verify_account(page, expected_account: str) -> None:
    await page.goto(CREATOR_HOME, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_function("() => /抖音号[：:]\\s*\\S+/.test(document.body.innerText)", timeout=60000)
    body = await page.locator("body").inner_text()
    identifiers = re.findall(r"抖音号[：:]\s*(\S+)", body)
    if identifiers != [expected_account]:
        raise RuntimeError("主页真实抖音号与 expected-account 不一致，禁止上传")


async def ai_declaration(page) -> dict:
    await page.get_by_text("请选择自主声明", exact=True).first.click(timeout=10000)
    dialog = page.locator(".semi-modal-content").filter(has_text="对作品内容添加声明").first
    await dialog.wait_for(state="visible", timeout=10000)
    radios, matches = dialog.locator(".semi-radio"), []
    for index in range(await radios.count()):
        radio = radios.nth(index)
        if await radio.is_visible():
            text = " ".join((await radio.inner_text()).split())
            if re.search(r"AI|人工智能|生成合成|合成生成", text, re.I):
                matches.append((text, radio))
    if len(matches) != 1:
        raise RuntimeError("无法唯一确认真实 AI 声明选项")
    selected, radio = matches[0]
    await radio.click(timeout=10000)
    await dialog.get_by_role("button", name="确定", exact=True).click(timeout=10000)
    await dialog.wait_for(state="hidden", timeout=10000)
    await page.get_by_text(selected, exact=True).first.wait_for(state="visible", timeout=10000)
    return {"selected": selected, "verified": True}


async def set_cover(page, path: str) -> dict:
    await page.evaluate("() => document.querySelectorAll('.shepherd-element,.shepherd-modal-overlay-container').forEach(e=>e.remove())")
    choose = page.get_by_text("选择封面", exact=True).first
    await choose.evaluate("e => e.scrollIntoView({block:'center',inline:'nearest'})")
    await choose.click(timeout=15000)
    modal = page.locator("div.dy-creator-content-modal").first
    await modal.wait_for(state="visible", timeout=20000)
    portrait = modal.get_by_text("设置竖封面", exact=True).first
    if await portrait.count():
        await portrait.click(timeout=5000)
    upload = modal.locator("input.semi-upload-hidden-input").nth(1)
    await upload.set_input_files(path)
    names = await upload.evaluate("el => [...el.files].map(file => file.name)")
    if names != [Path(path).name]:
        raise RuntimeError("自定义封面文件选择读回不一致")
    await page.wait_for_timeout(3000)
    await modal.get_by_role("button", name="完成", exact=True).first.click(timeout=10000)
    later = page.get_by_role("button", name="暂不设置", exact=True)
    try:
        await later.click(timeout=3000)
    except Exception:
        pass
    await modal.wait_for(state="detached", timeout=20000)
    return {"verified": True, "selected_filename": names[0], "evidence": "文件输入读回及完成封面弹窗"}


async def verify_location(page, location: str) -> dict:
    selects = page.locator('xpath=//*[normalize-space(.)="添加标签"]/following::*[contains(concat(" ", normalize-space(@class), " "), " semi-select ")]')
    selected = []
    for index in range(await selects.count()):
        item = selects.nth(index)
        if await item.is_visible():
            label = item.locator(".semi-select-selection-text").first
            text = await label.inner_text() if await label.count() else (await item.inner_text()).splitlines()[0]
            selected.append(text.strip())
    target = re.sub(r"\s+", "", location)
    matches = [text for text in selected if target == re.sub(r"\s+", "", text)]
    if len(matches) != 1 or "位置" not in selected:
        raise RuntimeError("添加标签行的实际 POI 与目标位置不一致")
    return {"verified": True, "selected": matches[0], "evidence": "添加标签行选中值读回"}


async def prepare(context, old_page, job: dict, app, expected_account: str, state_path: Path):
    page = await context.new_page()
    for field in ("file", "cover"):
        if sha256(Path(job[field])) != job[field + "_sha256"]:
            raise RuntimeError("准备上传前素材 hash 已改变")
    await app.validate_upload_args()
    await page.goto(UPLOAD, wait_until="domcontentloaded", timeout=90000)
    with contextlib.redirect_stdout(io.StringIO()):
        page = await app.ensure_logged_in_for_upload(context, page)
    await verify_account(page, expected_account)
    await page.goto(UPLOAD, wait_until="domcontentloaded", timeout=90000)
    upload = page.locator('input[type="file"][accept*="video"], input[type="file"][accept*="mp4"]').first
    await upload.set_input_files(job["file"], timeout=120000)
    await page.wait_for_url(re.compile(r"/content/(?:publish|post/video)"), timeout=120000)
    await page.locator('[class^="long-card"] div:has-text("重新上传")').first.wait_for(state="visible", timeout=180000)
    await app.fill_title_and_description(page, job["title"], job["desc"], job["tags"])
    await app.set_location(page, job["location"])
    location = await verify_location(page, job["location"])
    cover = await set_cover(page, job["cover"])
    declaration = await ai_declaration(page)
    title = page.locator('input[placeholder*="标题"]').first
    actual_title = await title.input_value()
    if not actual_title.strip():
        await app.fill_title_and_description(page, job["title"], job["desc"], job["tags"])
        actual_title = await title.input_value()
    if actual_title != job["title"][:30]:
        raise RuntimeError("准备后的实际标题与清单不一致")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot = state_path.parent / "prepared.png"
    await page.screenshot(path=str(screenshot), full_page=True)
    save(state_path, {"stage": "prepared", "fingerprint": job["fingerprint"], "title_verified": actual_title,
                      "declaration": declaration, "cover": cover, "location": location, "screenshot": str(screenshot)})
    if old_page and not old_page.is_closed():
        await old_page.close()
    return page


async def work_list(context) -> list[dict]:
    response = await context.request.get(WORK_LIST, timeout=5000)
    data = await response.json()
    if response.status != 200 or data.get("status_code", 0) != 0 or not isinstance(data.get("aweme_list"), list):
        raise RuntimeError("作品管理列表请求失败，不能建立发布回执")
    return data["aweme_list"]


def match_receipt(items: list[dict], job: dict, expected_account: str, excluded: set[str], submitted_after: int) -> dict | None:
    title, prefix = job["title"][:30], job["desc"].strip()[:24]
    matches = []
    for item in items:
        identifier = str(item.get("aweme_id") or "")
        if not identifier or identifier in excluded or item.get("item_title") != title:
            continue
        if not item.get("caption", "").strip().startswith(prefix) or item.get("create_time", 0) < submitted_after:
            continue
        if item.get("author", {}).get("unique_id") != expected_account:
            raise RuntimeError("新作品回执账号不匹配")
        matches.append(item)
    if len(matches) > 1:
        raise RuntimeError("多个新作品回执同时匹配，停止核查")
    if not matches:
        return None
    item = matches[0]
    return {"matched": True, "aweme_id": str(item["aweme_id"]), "title": item["item_title"],
            "caption": item["caption"], "create_time": item["create_time"], "account": expected_account,
            "status": item.get("status"), "poi_info": item.get("poi_info"),
            "ai_declaration": item.get("risk_infos", {}).get("content"), "evidence": "作品管理列表接口"}


async def receipt(context, job: dict, expected_account: str, excluded: set[str], submitted_after: int) -> dict:
    for attempt in range(6):
        try:
            items = await work_list(context)
        except Exception:
            items = []
        result = match_receipt(items, job, expected_account, excluded, submitted_after)
        if result:
            return result
        if attempt < 5:
            await asyncio.sleep(2)
    return {"matched": False, "error": "尚未读到符合账号、标题、文案和时间的新作品编号"}


async def publish(context, page, job: dict, expected_account: str, state_path: Path) -> dict:
    state = read(state_path)
    if state.get("stage") != "prepared" or state.get("fingerprint") != job["fingerprint"]:
        raise RuntimeError("没有同内容的 prepared 状态，禁止发布")
    try:
        if not all(state.get(key, {}).get("verified") for key in ("declaration", "cover", "location")):
            raise RuntimeError("AI声明、封面或 POI 未核验")
        if await page.locator('input[placeholder*="标题"]').first.input_value() != job["title"][:30]:
            raise RuntimeError("发布前标题读回不一致")
        await page.get_by_text(state["declaration"]["selected"], exact=True).first.wait_for(state="visible", timeout=5000)
        await verify_location(page, job["location"])
        button = page.get_by_role("button", name="发布", exact=True)
        await button.wait_for(state="visible", timeout=10000)
        baseline = await work_list(context)
        if any(item.get("author", {}).get("unique_id") != expected_account for item in baseline):
            raise RuntimeError("发布前作品列表作者账号与 expected-account 不一致")
        excluded = set(job["exclude_aweme_ids"]) | {str(item["aweme_id"]) for item in baseline if item.get("aweme_id")}
        submitted_after = int(time.time()) - 5
    except Exception as error:
        save(state_path, {**state, "stage": "prepare_failed", "error": str(error)})
        raise
    state.update(stage="submission_uncertain", submitted_after=submitted_after,
                 baseline_verified=True, exclude_aweme_ids=sorted(excluded))
    save(state_path, state)  # Must succeed before the one and only publish click.
    try:
        await button.click(timeout=10000)
        await page.wait_for_url("**/creator-micro/content/manage**", timeout=60000)
        result = await receipt(context, job, expected_account, excluded, submitted_after)
        stage = "submitted" if result["matched"] else "submitted_unverified"
        save(state_path, {**state, "stage": stage, "receipt": result})
        if not result["matched"]:
            raise RuntimeError("提交回执未核验，停止批量发布")
        return result
    except Exception as error:
        if read(state_path).get("stage") != "submitted":
            save(state_path, {**read(state_path), "stage": "submitted_unverified", "error": str(error)})
        raise


async def live(args, jobs: list[dict]) -> None:
    runtime = Path(os.environ.get("SOCIAL_AUTO_UPLOAD_HOME", Path.home() / ".openclaw/workspace/social-auto-upload")).expanduser()
    sys.path.insert(0, str(runtime))
    from patchright.async_api import async_playwright
    from uploader.douyin_uploader.main import (
        DouYinVideo, douyin_launch_args, douyin_profile_dir, hydrate_douyin_context_from_cookie_file,
    )
    from utils.base_social_media import set_init_script

    account_file = runtime / "cookies" / f"douyin_{args.account}.json"
    account_file.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(douyin_profile_dir(account_file)), channel="chrome", headless=False,
            args=douyin_launch_args(), permissions=["geolocation"],
        )
        try:
            context = await set_init_script(context)
            await hydrate_douyin_context_from_cookie_file(context, account_file)
            page = context.pages[0] if context.pages else await context.new_page()
            for index, job in enumerate(jobs, 1):
                state_path = args.state_dir / job["id"] / "state.json"
                save(state_path, {"stage": "preparing", "fingerprint": job["fingerprint"]})
                app = DouYinVideo(job["title"], job["file"], job["tags"], 0, str(account_file),
                                 thumbnail_portrait_path=job["cover"], desc=job["desc"], location=job["location"], headless=False)
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        page = await prepare(context, page, job, app, args.expected_account, state_path)
                except Exception as error:
                    save(state_path, {"stage": "prepare_failed", "fingerprint": job["fingerprint"], "error": str(error)})
                    raise
                await publish(context, page, job, args.expected_account, state_path)
                print(json.dumps({"completed": index, "total": len(jobs)}), flush=True)
        finally:
            await context.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.state_dir = args.state_dir.expanduser().resolve()
    try:
        jobs = load_manifest(args.manifest, args.account, args.expected_account)
        pending = pending_jobs(jobs, args.state_dir)
        if args.dry_run:
            print(json.dumps({"dry_run": True, "validated": len(jobs), "pending": len(pending), "already_submitted": len(jobs) - len(pending)}))
        elif pending:
            asyncio.run(live(args, pending))
        return 0
    except Exception as error:
        print(f"批量发布已停止：{type(error).__name__}: {error}；请核查清单和本地逐条状态，禁止盲目重试。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
