#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${CODEX_HOME:-$HOME/.codex}/skills/douyin-groupbuy-auto-publish"

mkdir -p "$(dirname "$target")"
rm -rf "$target"
mkdir -p "$target"

tar \
  --exclude ".git" \
  --exclude "__pycache__" \
  --exclude ".DS_Store" \
  -cf - -C "$repo_dir" . | tar -xf - -C "$target"

echo "installed douyin-groupbuy-auto-publish skill to $target"
