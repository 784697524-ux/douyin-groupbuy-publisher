---
name: douyin-groupbuy-auto-publish
description: 抖音团购选品图文/视频发布自动化。Automate Douyin local-life group-buy promotion from product selection to image-note or video publishing with a domestic POI. Use when Codex needs to publish Douyin 图文或视频并挂带货模式国内位置。
---

# 抖音团购选品图文/视频发布自动化

## Scope

Use this skill for the full Douyin local-life coupon promotion path:

1. Select ranked coupons from 抖音来客/生意经.
2. Convert selected product facts into public-facing title, caption, and image prompts.
3. Generate 3-5 poster images with the installed `imagegen` skill.
4. Publish the prepared image note with music, or publish one prepared video, with a domestic group-buy POI.

Do not expose backend product IDs, sales counts, ranking numbers, cookies, QR codes, or private logs in public captions or generated posters.

## Runtime

This skill wraps an existing `social-auto-upload` runtime.

Default runtime:

```bash
export SOCIAL_AUTO_UPLOAD_HOME="${SOCIAL_AUTO_UPLOAD_HOME:-$HOME/.openclaw/workspace/social-auto-upload}"
```

The runtime must provide:

```text
$SOCIAL_AUTO_UPLOAD_HOME/.venv/bin/sau
```

Before first use, apply the Douyin group-buy patch:

```bash
./scripts/apply_douyin_groupbuy_patch.sh
```

The patch also removes the separate cookie-preflight browsers. A publish command now opens one persistent Chrome profile, validates login inside that same session, and continues publishing after the operator scans or completes verification.

## Main Workflow

For video publishing, run `publish-video` directly. It uses the same account browser profile for login and upload. If Douyin shows a login page, the command waits in that same browser session and continues after the operator scans or completes verification. The profile lives under:

```text
$SOCIAL_AUTO_UPLOAD_HOME/browser_profiles/douyin_<douyin_account>/
```

First use of a new account/profile may require QR login. Subsequent publishes should reuse that profile unless Douyin triggers risk verification. Use `check` only for explicit account diagnostics:

```bash
./bin/sau douyin check --account <douyin_account>
```

Select products:

```bash
python scripts/douyin_groupbuy_pipeline.py select \
  --account <douyin_account> \
  --groupid <laike_groupid> \
  --date "昨天" \
  --metric "商品成交券数" \
  --limit 3 \
  --output /path/to/selection.json \
  --headed
```

If the standalone browser lands on the 生意经 login page, reuse an already logged-in Chrome session:

```bash
python scripts/douyin_groupbuy_pipeline.py select \
  --account <douyin_account> \
  --output /path/to/selection.json \
  --cdp-url http://127.0.0.1:9222 \
  --headed
```

Create the public work package:

```bash
python scripts/douyin_groupbuy_pipeline.py brief \
  --selection /path/to/selection.json \
  --product-index 1 \
  --output-dir /path/to/groupbuy_work \
  --location "合肥滨湖银泰百货"
```

Then read `/path/to/groupbuy_work/image_prompts.md` and use `imagegen` to generate 3-5 images into:

```text
/path/to/groupbuy_work/images/
```

Publish:

```bash
python scripts/douyin_groupbuy_pipeline.py publish \
  --account <douyin_account> \
  --work-dir /path/to/groupbuy_work \
  --location "合肥滨湖银泰百货" \
  --bgm auto \
  --headed
```

Publish video with the verified domestic group-buy POI flow:

```bash
./bin/douyin-groupbuy publish-video \
  --account <douyin_account> \
  --file /path/to/video.mp4 \
  --title "<作品标题>" \
  --desc "<作品简介>" \
  --tags "杭州中大银泰,杭州团购,本地生活" \
  --location "杭州中大银泰百货" \
  --headed
```

Do not run `douyin check` before every publish. Use it only for explicit account diagnostics; `publish-video` validates or refreshes the account in its single upload browser session. A 2026-09-15 local publish test with `六安银泰百货` confirmed the command continued after QR login and completed publishing.

## One-Step Selection And Brief

Use `run` to select products and create the work package in one command. It does not generate native images by itself.

```bash
python scripts/douyin_groupbuy_pipeline.py run \
  --account <douyin_account> \
  --groupid <laike_groupid> \
  --date "昨天" \
  --metric "商品成交券数" \
  --limit 3 \
  --output /path/to/selection.json \
  --product-index 1 \
  --output-dir /path/to/groupbuy_work \
  --location "合肥滨湖银泰百货" \
  --headed
```

## POI Rules

The publish path must use:

1. `添加标签` = `位置`
2. mode = `带货模式`
3. location tab = `国内`
4. type POI into the location input
5. wait for candidates
6. select a matching mall-level candidate

The video page may omit the second mode selector and show `输入地理位置` directly. The bundled video patch handles both page variants.

The bundled patch scores candidates so mall-level POIs like `合肥滨湖银泰百货` outrank shop-level POIs like `植村秀(滨湖银泰城店)`.

## Success Criteria

Selection succeeds only when the JSON contains ranked products and Laike detail facts.

Brief generation succeeds only when the work directory contains:

- `title.txt`
- `caption.txt`
- `image_prompts.md`
- `publish_meta.json`
- `images/`

Publishing succeeds only when the CLI exits successfully and Douyin reports a success state or enters the creator management page. If Douyin requests SMS verification or captcha, wait for the user/operator and do not report success early.

## References

- `references/usage.md`: user-facing install and CLI examples
- `references/automa-selectors.md`: proven music and location selectors
- `references/public-copy-rules.md`: public promotion copy rules
