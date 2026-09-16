#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


RUNTIME = Path(os.environ.get("SOCIAL_AUTO_UPLOAD_HOME", Path.home() / ".openclaw/workspace/social-auto-upload"))
SAU = RUNTIME / ".venv" / "bin" / "sau"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a Douyin group-buy image note with POI and music.")
    parser.add_argument("--account", required=True)
    parser.add_argument("--images", nargs="+", required=True)
    parser.add_argument("--title", required=True)
    note = parser.add_mutually_exclusive_group(required=True)
    note.add_argument("--note")
    note.add_argument("--notef")
    parser.add_argument("--location", required=True)
    parser.add_argument("--bgm", default="auto", help='Use "auto" to select the first recommended music.')
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--keep-open-on-error", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    images = [Path(image).expanduser() for image in args.images]
    missing = [str(image) for image in images if not image.is_file()]
    if missing:
        raise SystemExit("missing images:\n" + "\n".join(missing))

    cmd = [
        str(SAU),
        "douyin",
        "upload-note",
        "--account",
        args.account,
        "--images",
        *[str(image) for image in images],
        "--title",
        args.title,
        "--location",
        args.location,
        "--bgm",
        args.bgm,
    ]
    if args.note:
        cmd.extend(["--note", args.note])
    else:
        note_file = Path(args.notef).expanduser()
        if not note_file.is_file():
            raise SystemExit(f"missing note file: {note_file}")
        cmd.extend(["--notef", str(note_file)])
    cmd.append("--headless" if args.headless else "--headed")

    env = os.environ.copy()
    if args.keep_open_on_error:
        env["DOUYIN_KEEP_OPEN_ON_ERROR"] = "1"
    return subprocess.run(cmd, cwd=RUNTIME, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
