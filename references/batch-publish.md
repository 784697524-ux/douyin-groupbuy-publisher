# 批量视频发布 CLI

`publish-batch` 将已准备好的视频逐条发布，沿用同一个 Chrome Profile，保存每条作品的状态和平台提交回执。选品与详情仍使用 `select`、`detail`；图文仍使用 `publish`。

## 准备清单

创建 `batch.json`：

```json
{
  "items": [
    {
      "id": "coupon-video-01",
      "file": "videos/01.mp4",
      "cover": "covers/01.png",
      "title": "周末逛吃有新安排",
      "desc": "先看清卡券权益和可用门店，再安排周末逛吃。",
      "tags": ["本地生活", "逛吃卡"],
      "location": "示例商场完整名称",
      "exclude_aweme_ids": []
    }
  ]
}
```

相对素材路径从清单文件所在目录解析。每条必须有唯一 `id`、实际存在的视频和竖封面、非空标题与简介、完整 POI 名称。标题最长 30 字；简介建议 1–2 句。脚本不会自动改写文案或加工素材。只选用用户确认的原片或明确授权的配音版本。

可用 `source_sha256` 固定已确认视频的 SHA-256；不匹配时终止。`exclude_aweme_ids` 用于明确排除旧作品，尤其适用于获准重发的新版本。状态也保存素材和发布信息的指纹：同一 `id` 改了文件或文案，不能沿用旧回执跳过。

## 一条命令

运行环境使用现有 `social-auto-upload`，需要其 `.venv/bin/python`、Patchright、Chrome 和已安装的团购发布补丁：

```bash
export SOCIAL_AUTO_UPLOAD_HOME="${SOCIAL_AUTO_UPLOAD_HOME:-$HOME/.openclaw/workspace/social-auto-upload}"
./scripts/apply_douyin_groupbuy_patch.sh
./bin/douyin-groupbuy publish-batch \
  --manifest /path/to/batch.json --state-dir /path/to/batch-state \
  --account demo_douyin --expected-account demo_douyin_id \
  --dry-run
```

`--account` 是本地 Profile/Cookie 的别名；`--expected-account` 是创作者主页显示的真实抖音号。两者不必相同。正式执行先等待真实抖音号加载，再精确核对；不匹配就停止上传。新 Profile 可能需扫码或平台验证，仍在同一可见浏览器中处理。

`--dry-run` 校验全部素材、哈希、清单和续跑状态，不需要发布运行环境，不启动浏览器、不提交作品。确认清单并获准发布后，去掉该参数，其他参数保持一致。重跑同一命令即可接续。

## 实际执行与回执

整个批次复用一个持久 Chrome Context。每条作品先创建新编辑页，再关闭上一页，避免关掉最后一页导致会话退出。上传完成后填写文案、选择国内团购 POI、应用竖封面，并设置 AI 内容声明。提交前再次读取标题和声明，不能通过就停止。

竖封面应用成功指上传并完成封面弹窗操作；本地截图用于人工核对，自动化没有执行最终封面图像相似度检查。POI 是位置标签，不等同于绑定可购买的卡券商品链接。

点击发布前先保存当前作品 ID 集合和 `submission_uncertain` 状态，再点击一次发布按钮。回执从创作者作品列表读取，必须同时匹配：新作品 ID、标题、简介前缀、作者抖音号和提交时间，且不在旧作品排除列表中。管理页标题文字本身不能作为成功回执。

| 状态 | 含义 | 续跑行为 |
| --- | --- | --- |
| `prepared` | 素材与发布页准备完成 | 可重新准备，尚未提交 |
| `submitted` | 已获得匹配的新作品回执 | 同一内容跳过 |
| `submission_uncertain` | 已进入可能提交的阶段 | 停止，先查平台与本地证据 |
| `submitted_unverified` | 提交后未确认匹配回执 | 停止，不再点击发布 |
| `prepare_failed` | 上传或准备阶段失败 | 停止，修复后确认未提交再处理状态 |

状态、回执和截图保存在 `--state-dir`，属于私有运行证据。不要上传到共享代码仓库。遇到结果不明，不要删除状态、换空目录或自动重发来“试一下”。先核对平台是否已有作品；只有明确获准的新发布任务才创建新的发布状态包。

`submitted` 证明提交回执匹配。平台审核、公开可见、最终封面、视频播放和配音需要分别验收。当前 CLI 不自动下载平台视频做音轨验收。

## 2026-10-10 迁移范围

从当天已执行的单浏览器批量发布脚本迁移账号等待、竖封面、标题校验、AI 声明、单次点击、逐条回执和断点续跑；移除了具体任务目录、账号和作品清单。通用入口额外将账号校验前置，统一用严格新作品回执，避免旧同标题作品被误认为本次提交。视频生成、配音生产和平台音轨核验没有合并进发布命令。

来源脚本有当天的发布证据；通用 CLI 的移植验收采用离线清单校验和状态/浏览器接口测试，不为验收新增真实作品。
