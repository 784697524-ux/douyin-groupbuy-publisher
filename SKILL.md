---
name: douyin-groupbuy-auto-publish
description: 抖音团购选品、卡券详情和图文/视频发布 CLI。查询昨日销售金额前十、按商品 ID 读取来客详情、生成推广简报并发布到抖音国内带货位置。
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

## Verified Selection CLI

Selection uses OpenCLI's Chrome extension to reuse the logged-in Laike/Shengyijing browser. It does not need a creator account name or CDP port. Run `opencli doctor` first; proceed when the daemon and extension are connected. Keep the same `--session` throughout a query. The CLI activates and verifies its owned tab before page actions; an inactive bound tab that forbids selection fails with a request to use a fresh owned session. Read [references/selection-cli.md](references/selection-cli.md) for the recorded flow, supported scope, output, and troubleshooting.

```bash
./bin/douyin-groupbuy select \
  --groupid <laike_groupid> --date 昨天 \
  --metric 商品成交金额 --limit 10 --detail-index 1 \
  --output /path/to/selection.json
```

This checks the Asia/Shanghai yesterday period, exact metric column, active descending sort, loading completion, actual numeric order, and the selected coupon's detail identity. It reads ten ranking rows and only one detail. `--detail-index 0` saves ranking only. A saved ranking with `details_status=pending` is not a completed detail query.

Read another coupon without rerunning the ranking:

```bash
./bin/douyin-groupbuy detail \
  --selection /path/to/selection.json --product-index 3 \
  --output /path/to/detail.json
```

The OpenCLI selection currently supports `昨天`, amount or coupon-count ranking, and 1-10 rows on the first page. A blank detail gets one fresh-tab recovery using its verified URL, then readable fields must pass again. Login, wrong product, persistent blank detail, or insufficient rows fails; retain the saved ranking and report the failed stage. Use `--browser sau --account <account> --cdp-url <url>` only for the legacy selector, whose loose text parser is not the verified amount-ranking path.

## Main Workflow

For video publishing, run `publish-video` directly. It uses the same account browser profile for login and upload. If Douyin shows a login page, the command waits in that same browser session and continues after the operator scans or completes verification. The profile lives under:

```text
$SOCIAL_AUTO_UPLOAD_HOME/browser_profiles/douyin_<douyin_account>/
```

First use of a new account/profile may require QR login. Subsequent publishes should reuse that profile unless Douyin triggers risk verification. Use `check` only for explicit account diagnostics:

```bash
./bin/sau douyin check --account <douyin_account>
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

Use `run` to select ten products, verify the chosen detail, and create a work package in one command. It does not generate native images by itself; use `imagegen` in Codex. To publish prepared assets in the same command, supply `--account`, `--images` (or `--image-dir`), and `--publish`. Add `--dry-run` to validate assets and print the upload command without submitting. Native image generation remains a separate agent step; do not report it as a shell-only generation workflow.

```bash
python scripts/douyin_groupbuy_pipeline.py run \
  --groupid <laike_groupid> \
  --date "昨天" \
  --metric "商品成交金额" \
  --limit 10 \
  --output /path/to/selection.json \
  --product-index 1 \
  --output-dir /path/to/groupbuy_work \
  --location "合肥滨湖银泰百货" \
  --headed
```

## POI Rules

A domestic POI is a location tag. It does not prove the selected coupon is attached as a purchasable product; the existing note publisher attaches the POI. The publish path must use:

1. `添加标签` = `位置`
2. mode = `带货模式`
3. location tab = `国内`
4. type POI into the location input
5. wait for candidates
6. select a matching mall-level candidate

The video page may omit the second mode selector and show `输入地理位置` directly. The bundled video patch handles both page variants.

The bundled patch scores candidates so mall-level POIs like `合肥滨湖银泰百货` outrank shop-level POIs like `植村秀(滨湖银泰城店)`.

## Success Criteria

Selection succeeds only when the JSON contains the verified date/metric/order and requested rows; a requested detail additionally requires `details_status=verified`, matching product ID in the detail URL, and readable product fields, completed loading, and a displayed store count matching the product's store references. Report ranking, detail, assets, and publication separately. `timings` records measured seconds; do not infer speed from fixed waits or old runs.

Brief generation succeeds only when the work directory contains:

- `title.txt`
- `caption.txt`
- `image_prompts.md`
- `publish_meta.json`
- `images/`

Publishing succeeds only when the CLI exits successfully and Douyin reports a success state or enters the creator management page. If Douyin requests SMS verification or captcha, wait for the user/operator and do not report success early.

## References

- `references/selection-cli.md`: verified ranking/detail CLI and Automa migration
- `references/usage.md`: user-facing install and CLI examples
- `references/automa-selectors.md`: proven music and location selectors
- `references/public-copy-rules.md`: public promotion copy rules
