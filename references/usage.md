# 使用说明

![抖音团购选品图文发布自动化流程](../assets/workflow.svg)

这个技能做三件事：先从抖音来客/生意经选出值得推广的卡券，再生成公开内容，最后自动发布抖音图文或视频并挂团购位置。

## 1. 应用 runtime patch

```bash
./scripts/apply_douyin_groupbuy_patch.sh
```

这一步会同时安装单浏览器和持久登录 Profile 修复。后续每次视频发布只启动一个 Chrome 会话，不再先连续打开多个窗口做 cookie 预检。

它会给本机 `social-auto-upload` runtime 增加：

- `sau douyin select-groupbuy-products`
- `sau douyin upload-note --location --bgm`
- `sau douyin upload-video --location`
- 抖音发布页国内 POI 选择逻辑
- 发布视频时使用 `$SOCIAL_AUTO_UPLOAD_HOME/browser_profiles/douyin_<账号名>/` 复用登录态

![命令和产物关系](../assets/usage-map.svg)

## 2. 昨日金额前十与一张详情

```bash
opencli doctor
./bin/douyin-groupbuy select --groupid <laike_groupid> \
  --date 昨天 --metric 商品成交金额 --limit 10 --detail-index 1 \
  --output /path/to/selection.json
```

无需创作者账号或CDP端口。复用Chrome登录态，验证单日周期、金额列、降序和商品身份，只查询所选一张详情。详细说明与录制迁移见 [selection-cli.md](selection-cli.md)。

当前支持“昨天”、商品成交金额/商品成交券数和前 1-10 条。需要查看榜单中的另一张卡券时：

```bash
./bin/douyin-groupbuy detail --selection /path/to/selection.json \
  --product-index 3 --output /path/to/detail.json
```

`--cdp-url` 仅用于显式选择 `--browser sau --account demo_douyin` 的旧版选品器，默认 OpenCLI 查询不使用它。

## 3. 生成图文 brief

```bash
python scripts/douyin_groupbuy_pipeline.py brief \
  --selection /path/to/selection.json \
  --product-index 1 \
  --output-dir /path/to/groupbuy_work \
  --location "<完整POI名称>"
```

生成内容：

- `title.txt`
- `caption.txt`
- `image_prompts.md`
- `publish_meta.json`
- `images/`

也可用 `run` 将选品和生成工作包串成一步：

```bash
./bin/douyin-groupbuy run --groupid <laike_groupid> \
  --date 昨天 --metric 商品成交金额 --limit 10 --product-index 1 \
  --output /path/to/selection.json --output-dir /path/to/groupbuy_work \
  --location "<完整POI名称>"
```

`run` 不会自动生成图片；已有真实素材时，可增加 `--images <图片...> --account demo_douyin --publish` 接续发布，用 `--dry-run` 校验而不提交。

## 4. 生图

用 Codex 的 `imagegen` skill 读取 `image_prompts.md`。每个商品建议生成 3-5 张竖版图文海报，放入 `images/`。

## 5. 发布

```bash
python scripts/douyin_groupbuy_pipeline.py publish \
  --account demo_douyin \
  --work-dir /path/to/groupbuy_work \
  --location "<完整POI名称>" \
  --bgm auto \
  --headed
```

![团购位置选择示意](../assets/poi-location.svg)

首次跑新账号、新 Profile 或新 POI 时保留 `--headed`，便于观察扫码、验证码、风控和位置候选。

## 6. 发布视频

```bash
./bin/douyin-groupbuy publish-video \
  --account demo_douyin \
  --file /path/to/video.mp4 \
  --title "<作品标题>" \
  --desc "<作品简介>" \
  --tags "杭州中大银泰,杭州团购,本地生活" \
  --location "杭州中大银泰百货" \
  --headed
```

正常发布直接运行上面的命令即可，不要在每次发布前额外执行 `douyin check`。新账号第一次可能需要扫码；扫码完成后，当前发布命令会继续上传、挂位置并发布。后续同账号通常复用本地 Profile 登录态，除非抖音触发风控或短信验证码。

CLI 会完成：上传视频、选择位置、进入国内位置、输入门店、等待候选、选择匹配门店并发布。只有日志出现目标门店且平台返回发布成功，才算完成。
