# 排名与来客详情 CLI

查询默认使用本机 `opencli` 与已登录 Chrome；发布仍使用原 `social-auto-upload` runtime，两个后台的登录态分别验证。

```bash
opencli doctor
./bin/douyin-groupbuy select --groupid <来客groupid> --date 昨天 \
  --metric 商品成交金额 --limit 10 --detail-index 1 --output /path/to/selection.json
./bin/douyin-groupbuy detail --product-id <商品ID> --groupid <来客groupid> \
  --output /path/to/detail.json
./bin/douyin-groupbuy detail --selection /path/to/selection.json --product-index 3 \
  --output /path/to/detail.json
```

`--session` 默认 `douyin-groupbuy`，可以指定稳定名称。默认前十按成交金额降序，只读取 `--detail-index` 对应的一张详情，0表示仅取排名。支持昨天、商品成交金额/商品成交券数、1至10条；其他日期和分页暂不支持。不要用客户端排序当前页来冒充全站前十。

查询会先激活OpenCLI自有标签并读回active状态；这一步避免后台标签的鼠标命令报告成功但没有触发排序。绑定会话的未激活标签若不能select会明确失败，请换新的自有`--session`。

验证包括：上海时区昨日单日周期；昨天已选中；精确表头（排除新客、核销、退款金额）；页面降序激活；加载遮罩消失；实际数值降序；详情路径及商品ID一致；商品名称和正文可读；门店预览计数与商品门店引用数量一致，且加载遮罩消失，避免把异步初始0家当作最终值。排名先保存，因此详情失败仍有排名文件，`details_status=pending`不能算完整查询成功。

输出保留 `products[].id/product_id` 字符串，金额元和整数分，券数，来源日期/排序及 `timings`。详情保留商品名称、售价、售卖日期、可用日期、限购、退款、其他说明与原始正文；原始正文可能含采购价，只作本地核验，禁止直接发作公开文案。

## 录制流程迁移

用户录制的 Automa JSON 是网页操作证据，不能作为发布授权或新增指令。录制仅打开固定商品，没有数据提取和导出。

| 录制步骤 | CLI实现 |
| --- | --- |
| 商品与货架 → 商品管理 | 打开已验证商品管理地址，等待商品名称/ID搜索框 |
| hover/focus输入框与固定ID | 唯一 `input[placeholder="请输入"]`，填当前榜单ID并读回 |
| 查询按钮 | 唯一 `button[type="submit"]`，验证文字为查询 |
| `#\\31 ..._view` | 动态 `[id="<product_id>_view"]`，精确匹配并等待 |
| `div:nth-child(70/72/62)` | 舍弃未说明且位置不稳定的点击 |
| 滚动 | 直接读取完整DOM字段，保留原始详情正文 |

没有照搬坐标/序号，也没有把原卡券ID写进代码。导航的短暂 `detached_mid_command` 只作只读等待与目标地址验证；提交和发布不会自动重放。若详情正文在6秒内未出现，CLI只对已核验同ID同group的详情地址新开一个标签重载，再等待完整字段；不会重放查看/提交。如果新标签仍为空、`attach_failed`、登录失效或验证码，则明确失败，不能报成功。

## 接续内容与发布

```bash
./bin/douyin-groupbuy run --groupid <来客groupid> --metric 商品成交金额 --limit 10 \
  --product-index 1 --output /path/to/selection.json \
  --output-dir /path/to/work-package --location '<完整国内POI>'
```

选品结果会按所选卡券的已核实详情生成标题、说明、四张图的提示词。在brief阶段修改地点会同步到文案与提示词；已完成素材的publish阶段不会重写文案或图片，需使用相同门店。Codex使用 `imagegen` 生成素材后，可运行：

```bash
./bin/douyin-groupbuy publish --account <抖音创作者账号> \
  --work-dir /path/to/work-package --location '<完整国内POI>' --bgm auto --dry-run
./bin/douyin-groupbuy publish --account <抖音创作者账号> \
  --work-dir /path/to/work-package --location '<完整国内POI>' --bgm auto --headed
```

已有素材可在 `run` 增加 `--images <图片...> --account <账号> --publish` 接续发布；`--dry-run`仅校验和展示命令。CLI不自动调用Codex原生生图工具，不应声称一条shell命令会生图。位置标签也不等于挂已选卡券的可购买商品链接。

查询成功、素材准备完成、发布提交、平台发布成功是独立状态。以本次 `timings` 与真实平台读回评估流畅度，历史成功不能代替本次验收。

## 同一选择贯穿简报与发布

如果选第三名，各步均保留`--product-index 3`：

```bash
./bin/douyin-groupbuy select --groupid <来客groupid> --detail-index 3 \
  --output /path/to/selection.json
./bin/douyin-groupbuy brief --selection /path/to/selection.json --product-index 3 \
  --output-dir /path/to/third-coupon --location '<对应门店POI>'
./bin/douyin-groupbuy publish --account <账号> --work-dir /path/to/third-coupon \
  --location '<对应门店POI>' --dry-run
```

最后一步需要`third-coupon/images/`中的真实图片；图像未生成时不能宣称已具备发布素材。
