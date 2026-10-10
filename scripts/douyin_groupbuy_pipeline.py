#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from groupbuy_browser import Browser, select_rank
from laike_detail import read_detail, build_product_copy


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ.get("SOCIAL_AUTO_UPLOAD_HOME", Path.home() / ".openclaw/workspace/social-auto-upload"))
SAU = RUNTIME / ".venv" / "bin" / "sau"
DEFAULT_GROUP_ID = "1748531528342532"


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, cwd=RUNTIME)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def collect_images(image_dir: Path | None, images: list[str]) -> list[Path]:
    if images:
        return [Path(image).expanduser() for image in images]
    if not image_dir:
        return []
    files: list[Path] = []
    for suffix in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        files.extend(sorted(image_dir.expanduser().glob(suffix)))
    return files


def select_products(args: argparse.Namespace) -> Path:
    output = args.output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.browser == "opencli":
        browser = Browser(args.session)
        selection = select_rank(browser, args.groupid, args.date, args.metric, args.limit)
        selection["details_status"] = "not_requested" if args.detail_index == 0 else "pending"
        write_text(output, json.dumps(selection, ensure_ascii=False, indent=2))
        if args.detail_index:
            if not 1 <= args.detail_index <= len(selection["products"]):
                raise SystemExit("--detail-index must be between 0 and --limit")
            product = selection["products"][args.detail_index - 1]
            started = time.monotonic()
            product["laike_detail"] = read_detail(browser, args.groupid, product["product_id"])
            product.update(build_product_copy(product, product["laike_detail"]))
            selection["timings"]["detail_seconds"] = round(time.monotonic() - started, 3)
            selection["timings"]["total_seconds"] += selection["timings"]["detail_seconds"]
            selection["details_status"] = "verified"
            selection["detail_index"] = args.detail_index
            write_text(output, json.dumps(selection, ensure_ascii=False, indent=2))
        print(json.dumps({"output": str(output), "products": len(selection["products"]),
                          "details_status": selection["details_status"], "timings": selection["timings"]}, ensure_ascii=False))
        return output
    if not args.account:
        raise SystemExit("--account is required for --browser sau")
    cmd = [
        str(SAU),
        "douyin",
        "select-groupbuy-products",
        "--account",
        args.account,
        "--groupid",
        args.groupid,
        "--date",
        args.date,
        "--metric",
        args.metric,
        "--limit",
        str(args.limit),
        "--output",
        str(output),
        "--debug",
    ]
    if args.cdp_url:
        cmd.extend(["--cdp-url", args.cdp_url])
    cmd.append("--headless" if args.headless else "--headed")
    run(cmd)
    return output


def query_detail(args: argparse.Namespace) -> Path:
    if args.selection:
        selection = load_json(args.selection.expanduser())
        if not 1 <= args.product_index <= len(selection.get("products", [])):
            raise SystemExit("--product-index is outside the selected products")
        product = selection["products"][args.product_index - 1]
        product_id = product.get("product_id") or product["id"]
        groupid = selection.get("groupid") or args.groupid
    else:
        product_id, groupid = args.product_id, args.groupid
    started = time.monotonic()
    detail = read_detail(Browser(args.session), groupid, product_id)
    detail["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if args.selection:
        product["laike_detail"] = detail
        product.update(build_product_copy(product, detail))
        selection["details_status"] = "verified"
        selection["detail_index"] = args.product_index
        write_text(args.selection.expanduser(), json.dumps(selection, ensure_ascii=False, indent=2))
    write_text(args.output.expanduser(), json.dumps(detail, ensure_ascii=False, indent=2))
    print(json.dumps({"output": str(args.output), "verified": detail["verified"],
                      "elapsed_seconds": detail["elapsed_seconds"]}, ensure_ascii=False))
    return args.output


def build_brief(args: argparse.Namespace) -> Path:
    selection = load_json(args.selection.expanduser())
    products = selection.get("products") or []
    if not products:
        raise SystemExit(f"selection has no products: {args.selection}")
    if not 1 <= args.product_index <= len(products):
        raise SystemExit("--product-index is outside the selected products")
    product = products[args.product_index - 1]
    detail = product.get("laike_detail")
    if not detail or not detail.get("verified"):
        raise SystemExit("selected product has no verified detail; select it with --detail-index")
    product.update(build_product_copy(product, detail, args.location))
    public = product.get("public") or {}
    prompts = product.get("image_prompts") or []
    out_dir = args.output_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    title = args.title or public.get("title") or "本地卡券先囤"
    caption = public.get("caption") or "本地生活卡券福利，下单前请看清可用门店、使用日期和退款规则。"
    location = args.location or public.get("location") or ""

    write_text(out_dir / "title.txt", title)
    write_text(out_dir / "caption.txt", caption)
    write_text(
        out_dir / "image_prompts.md",
        "\n\n".join(f"## 图 {index}\n{prompt}" for index, prompt in enumerate(prompts, 1)),
    )
    write_text(
        out_dir / "publish_meta.json",
        json.dumps(
            {
                "selection": str(args.selection.expanduser()),
                "product_index": args.product_index,
                "product_name": product.get("name", ""),
                "location": location,
                "title": title,
                "images_dir": str(out_dir / "images"),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    (out_dir / "images").mkdir(exist_ok=True)
    print(f"Group-buy brief saved: {out_dir}")
    return out_dir


def publish_note(args: argparse.Namespace) -> None:
    if not args.account:
        raise SystemExit("--account is required for publishing")
    work_dir = args.work_dir.expanduser()
    meta_path = work_dir / "publish_meta.json"
    meta = load_json(meta_path) if meta_path.exists() else {}
    image_paths = collect_images(args.image_dir, args.images)
    if not image_paths:
        image_paths = collect_images(work_dir / "images", [])
    missing = [str(path) for path in image_paths if not path.is_file()]
    if missing:
        raise SystemExit("missing images:\n" + "\n".join(missing))
    if not image_paths:
        raise SystemExit(f"no images found under {work_dir / 'images'}")

    title = args.title or (work_dir / "title.txt").read_text(encoding="utf-8").strip() or meta.get("title", "")
    note_file = args.notef or work_dir / "caption.txt"
    if not title.strip():
        raise SystemExit("missing publish title")
    note_file = note_file.expanduser()
    if not note_file.is_file() or not note_file.read_text(encoding="utf-8").strip():
        raise SystemExit(f"missing or empty caption: {note_file}")
    location = args.location or meta.get("location", "")
    if not location:
        raise SystemExit("missing --location and publish_meta.json has no location")

    cmd = [
        str(SAU),
        "douyin",
        "upload-note",
        "--account",
        args.account,
        "--images",
        *[str(path) for path in image_paths],
        "--title",
        title,
        "--notef",
        str(note_file),
        "--location",
        location,
        "--bgm",
        args.bgm,
    ]
    cmd.append("--headless" if args.headless else "--headed")
    if getattr(args, "dry_run", False):
        print(json.dumps({"status": "prepared_not_published", "command": cmd}, ensure_ascii=False))
        return
    run(cmd)


def publish_video(args: argparse.Namespace) -> None:
    video = args.file.expanduser()
    if not video.is_file():
        raise SystemExit(f"video not found: {video}")
    if not args.location.strip():
        raise SystemExit("missing --location")

    cmd = [
        str(SAU),
        "douyin",
        "upload-video",
        "--account",
        args.account,
        "--file",
        str(video),
        "--title",
        args.title,
        "--location",
        args.location,
    ]
    if args.desc:
        cmd.extend(["--desc", args.desc])
    if args.tags:
        cmd.extend(["--tags", args.tags])
    if args.thumbnail:
        thumbnail = args.thumbnail.expanduser()
        if not thumbnail.is_file():
            raise SystemExit(f"thumbnail not found: {thumbnail}")
        cmd.extend(["--thumbnail", str(thumbnail)])
    if args.debug:
        cmd.append("--debug")
    cmd.append("--headless" if args.headless else "--headed")
    if getattr(args, "dry_run", False):
        print(json.dumps({"status": "prepared_not_published", "command": cmd}, ensure_ascii=False))
        return
    run(cmd)


def publish_batch(args: argparse.Namespace) -> None:
    python = Path(sys.executable) if args.dry_run else RUNTIME / ".venv/bin/python"
    cmd = [str(python), str(ROOT / "scripts/publish_douyin_groupbuy_batch.py"),
           "--manifest", str(args.manifest.expanduser().resolve()),
           "--state-dir", str(args.state_dir.expanduser().resolve()),
           "--account", args.account, "--expected-account", args.expected_account]
    if args.dry_run:
        cmd.append("--dry-run")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def run_pipeline(args: argparse.Namespace) -> None:
    if args.detail_index is None:
        args.detail_index = args.product_index
    selection = select_products(args)
    brief_args = argparse.Namespace(
        selection=selection,
        product_index=args.product_index,
        output_dir=args.output_dir,
        title=args.title,
        location=args.location,
    )
    work_dir = build_brief(brief_args)
    if args.publish:
        publish_args = argparse.Namespace(
            account=args.account,
            work_dir=work_dir,
            image_dir=args.image_dir,
            images=args.images,
            title=args.title,
            notef=None,
            location=args.location,
            bgm=args.bgm,
            headless=args.headless,
            dry_run=args.dry_run,
        )
        publish_note(publish_args)


def add_common_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--account", default="")
    parser.add_argument("--groupid", default=DEFAULT_GROUP_ID)
    parser.add_argument("--date", default="昨天")
    parser.add_argument("--metric", default="商品成交金额")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cdp-url", default="")
    parser.add_argument("--browser", choices=["opencli", "sau"], default="opencli")
    parser.add_argument("--session", default="douyin-groupbuy")
    parser.add_argument("--detail-index", type=int, default=1, help="Read only this ranked product; 0 = ranking only")
    runtime = parser.add_mutually_exclusive_group()
    runtime.add_argument("--headed", dest="headless", action="store_false")
    runtime.add_argument("--headless", dest="headless", action="store_true")
    parser.set_defaults(headless=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Douyin local group-buy selection -> brief -> publish pipeline.")
    commands = parser.add_subparsers(dest="command", required=True)

    select_parser = commands.add_parser("select", help="Fetch ranked coupons and Laike detail JSON")
    add_common_selection_args(select_parser)

    detail_parser = commands.add_parser("detail", help="Read one Laike coupon by product ID or saved ranking")
    detail_source = detail_parser.add_mutually_exclusive_group(required=True)
    detail_source.add_argument("--product-id")
    detail_source.add_argument("--selection", type=Path)
    detail_parser.add_argument("--product-index", type=int, default=1)
    detail_parser.add_argument("--groupid", default=DEFAULT_GROUP_ID)
    detail_parser.add_argument("--session", default="douyin-groupbuy")
    detail_parser.add_argument("--output", type=Path, required=True)

    brief_parser = commands.add_parser("brief", help="Create public copy and native-image prompts from selection JSON")
    brief_parser.add_argument("--selection", type=Path, required=True)
    brief_parser.add_argument("--product-index", type=int, default=1)
    brief_parser.add_argument("--output-dir", type=Path, required=True)
    brief_parser.add_argument("--title", default="")
    brief_parser.add_argument("--location", default="")

    publish_parser = commands.add_parser("publish", help="Publish prepared images with BGM and group-buy POI")
    publish_parser.add_argument("--account", required=True)
    publish_parser.add_argument("--work-dir", type=Path, required=True)
    publish_parser.add_argument("--image-dir", type=Path)
    publish_parser.add_argument("--images", nargs="*", default=[])
    publish_parser.add_argument("--title", default="")
    publish_parser.add_argument("--notef", type=Path)
    publish_parser.add_argument("--location", default="")
    publish_parser.add_argument("--bgm", default="auto")
    publish_parser.add_argument("--dry-run", action="store_true", help="Validate assets and print publish command without submitting")
    publish_runtime = publish_parser.add_mutually_exclusive_group()
    publish_runtime.add_argument("--headed", dest="headless", action="store_false")
    publish_runtime.add_argument("--headless", dest="headless", action="store_true")
    publish_parser.set_defaults(headless=False)

    video_parser = commands.add_parser(
        "publish-video",
        help="Publish one video with the domestic group-buy POI flow",
    )
    video_parser.add_argument("--account", required=True)
    video_parser.add_argument("--file", type=Path, required=True)
    video_parser.add_argument("--title", required=True)
    video_parser.add_argument("--desc", default="")
    video_parser.add_argument("--tags", default="")
    video_parser.add_argument("--location", required=True)
    video_parser.add_argument("--thumbnail", type=Path)
    video_parser.add_argument("--debug", action="store_true")
    video_parser.add_argument("--dry-run", action="store_true")
    video_runtime = video_parser.add_mutually_exclusive_group()
    video_runtime.add_argument("--headed", dest="headless", action="store_false")
    video_runtime.add_argument("--headless", dest="headless", action="store_true")
    video_parser.set_defaults(headless=False)

    batch_parser = commands.add_parser("publish-batch", help="Publish prepared videos in one Chrome session with resumable receipts")
    batch_parser.add_argument("--manifest", type=Path, required=True)
    batch_parser.add_argument("--state-dir", type=Path, required=True)
    batch_parser.add_argument("--account", required=True, help="Local Chrome profile/cookie alias")
    batch_parser.add_argument("--expected-account", required=True, help="Exact Douyin ID to verify before uploading")
    batch_parser.add_argument("--dry-run", action="store_true", help="Validate assets and resume state without opening Chrome or submitting")

    run_parser = commands.add_parser("run", help="Select products, create brief, and optionally publish")
    add_common_selection_args(run_parser)
    run_parser.add_argument("--product-index", type=int, default=1)
    run_parser.add_argument("--output-dir", type=Path, required=True)
    run_parser.add_argument("--title", default="")
    run_parser.add_argument("--location", default="")
    run_parser.add_argument("--image-dir", type=Path)
    run_parser.add_argument("--images", nargs="*", default=[])
    run_parser.add_argument("--bgm", default="auto")
    run_parser.add_argument("--publish", action="store_true")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(detail_index=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "select":
        select_products(args)
    elif args.command == "detail":
        query_detail(args)
    elif args.command == "brief":
        build_brief(args)
    elif args.command == "publish":
        publish_note(args)
    elif args.command == "publish-video":
        publish_video(args)
    elif args.command == "publish-batch":
        publish_batch(args)
    elif args.command == "run":
        run_pipeline(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, TimeoutError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
