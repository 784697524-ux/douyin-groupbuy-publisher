# Automa 关键选择器

这些选择器来自已成功发布的 Automa 流程，Python 自动化优先复用它们。

## 图文发布

- 打开发布页：`https://creator.douyin.com/creator-micro/content/upload`
- 图文 tab：`div.tab-item-BcCLTS:nth-child(2)`
- 上传 input：`.semi-tabs-pane-active input`
- 标题 input：`input.semi-input`

## 音乐

- 选择音乐入口：`span.action-Q1y01k`
- 第一首推荐音乐使用按钮：`.card-container-tmocjc:nth-child(1) .semi-button-content`

## 团购位置

- 地理位置入口：`.select-Ht3mEC .semi-select-selection-text`
- 国内 tab：`div.item-text-normal-MC1UEg`
- 位置输入框：`.semi-select-input > .semi-input`
- 候选项：`.semi-select-option-focused .detail-v2-uZaTIm`

视频发布页可能不显示第二个“带货模式”下拉框，而是在选择“位置”后直接出现“输入地理位置”。脚本会点击这个文字入口，再切换“国内”并输入门店。

## 注意

选择音乐后，抖音页面可能把 `添加标签` 类型切到 `游戏手柄`。发布前必须重新设置为 `位置`，再选择 `带货模式` 和 `国内`。
