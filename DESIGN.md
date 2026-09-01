---
name: 信息收集工作台
description: A precise operations workspace for enterprise discovery and ICP filing verification.
colors:
  primary: "#0F766E"
  primary-hover: "#0D665F"
  primary-soft: "#E7F3F1"
  canvas: "#F4F6F6"
  surface: "#FFFFFF"
  surface-subtle: "#F8FAFA"
  ink: "#162020"
  muted: "#5E6C6B"
  border: "#DCE4E3"
  success: "#15803D"
  warning: "#B45309"
  error: "#C2413A"
  dark-canvas: "#101414"
  dark-surface: "#171C1C"
typography:
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "24px"
    fontWeight: 650
    lineHeight: 1.35
    letterSpacing: "0"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "0"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "0"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0"
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "0 18px"
    height: "36px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0 11px"
    height: "36px"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "24px"
---

# Design System：信息收集工作台

> 最后更新：2026-09-01

## Overview

**Creative North Star: “作业台账”**

界面是一张结构严谨、持续更新的核验台账。用户首先看到当前任务、状态、耗时和数据范围；装饰只用于帮助定位可操作内容。

**Key Characteristics**

- 专业、克制、准确。
- 数据表格紧凑但不牺牲可读性。
- 查询状态、错误和完整性优先于装饰。
- 一级页面稳定：ICP备案查询、历史查询、基础配置。
- 浅色侧栏和白色工作面为默认视觉；深色模式遵循相同层级。

## Color

深青色只用于主操作、当前导航、链接和焦点。成功、警告、错误使用固定语义色，并始终搭配文字。

### Primary

- **Operational Teal** `#0F766E`：查询按钮、选中导航、链接、焦点。
- **Teal Wash** `#E7F3F1`：选中背景和低强调状态。

### Neutral

- **Work Canvas** `#F4F6F6`：页面画布。
- **Primary Surface** `#FFFFFF`：表单、结果和配置工作面。
- **Quiet Surface** `#F8FAFA`：表头、摘要和次级区域。
- **Ledger Ink** `#162020`：主文字。
- **Muted Copy** `#5E6C6B`：辅助信息。
- **Structure Line** `#DCE4E3`：边框和分隔线。

### Rule

**The Ten Percent Rule.** 主色面积保持稀缺；不能用大面积青色或装饰渐变制造“科技感”。

## Typography

使用系统无衬线字体，不引入展示型字体。

- Headline：24px / 650 / 1.35，仅用于真正的页面级标题。
- Title：16px / 600 / 1.5，用于卡片标题和任务主体。
- Body：14px / 400 / 1.6，用于表格和说明。
- Label：13px / 500 / 1.4，用于字段名和紧凑操作。

数据页不通过夸张字号建立层级；企业名称、数量、状态和错误必须最容易扫描。

## Layout

### App Shell

- 左侧浅色导航承载三个一级页面。
- 桌面端可折叠到图标宽度；窄屏收进抽屉。
- 顶部使用面包屑显示“信息收集工作台 / 当前页面”。
- 页面内容区固定在视口内，表格内容独立滚动。

### ICP备案查询

- 第一行是紧凑查询表单和最近查询。
- 第二块是单个结果卡片。
- 结果卡头展示企业、状态、查询用时和Excel导出。
- “对外投资 / ICP备案”使用分段控件切换。
- 表格滚动区与分页区分离；分页始终固定在卡片底部且可见。

### 历史查询

- 列表态：筛选工具栏 + 一张历史表格 + 一套分页。
- 详情态：历史列表被详情完全替换，只显示“返回列表”与查询结果。
- 禁止详情和历史列表上下同时出现，避免两套表格和两套分页竞争注意力。

### 基础配置

- 使用小尺寸分段控件切换“数据源配置 / 云函数配置”。
- 云函数配置保持紧凑，主要操作始终在页面上方可见。
- 配置页不展示用户无需理解的镜像或部署后函数地址。

## Components

### Buttons

- 32px或36px高度，6px圆角。
- 每个区域只有一个主按钮。
- 返回列表使用带左箭头的文字按钮。
- 删除操作使用危险色并配合二次确认。

### Status

- 成功、部分成功、失败、查询中、等待中、已取消使用固定中文标签。
- 不依赖颜色单独表达状态。
- 详情头同时展示状态和查询用时。

### Tables

- 表头固定，表格内容区域滚动。
- 默认20条/页，可选10、20、50。
- 历史列表使用服务端分页；结果详情使用本地分页。
- 列宽优先保证状态、时间和更新时间不换行；长企业名和域名使用省略。
- 分页范围使用 `起始-结束 / 总数`。

### Result Detail

- 对外投资列：投资方、被投企业、持股、层级、来源。
- ICP列：公司、主体备案号、服务备案号、域名、主办单位性质、更新时间。
- 原始 JSON 不进入主表格流。
- 导出生成一个Excel兼容工作簿，包含两张结果表。

### Forms

- 白底、1px中性边框、6px圆角。
- 查询表单横向排列，窄屏才换行。
- 错误状态使用清晰边框；具体原因通过消息或警告显示。
- 禁用控件保持可读，但不能像可操作控件。

## Elevation

静态工作面主要依靠背景和边框分层，不使用厚重阴影。

- 抽屉和浮层可使用 `0 12px 36px rgba(18, 32, 31, 0.14)`。
- 普通卡片默认无阴影。
- 圆角最大8px。

## Motion

- 状态切换控制在150～250ms。
- 动画只表达展开、切换、加载或反馈。
- 遵循 `prefers-reduced-motion`。
- 不使用编排式页面入场动画。

## Accessibility

- 正文与控件达到 WCAG 2.2 AA 对比度。
- 关键操作支持键盘和可见焦点。
- 图标按钮必须有 `aria-label`。
- 状态使用颜色、图标和文字共同表达。
- 紧凑布局不能缩小核心点击目标。

## Do

- 使用 `#0F766E` 统一表达主操作、当前选择和焦点。
- 明确区分任务合计、企业数量和分页范围。
- 保持分页可见，让表格内容在分页上方滚动。
- 历史详情只显示一个任务面，并提供明确返回入口。
- 失败、部分完成、限流和结果不完整必须显性显示。
- 在窄屏改变布局结构，而不是缩小文字。

## Don't

- 不做营销Hero、渐变背景、玻璃卡片或发光装饰。
- 不创建同质卡片墙。
- 不在同一页面同时显示历史列表和记录详情。
- 不把原始 JSON 铺进主表格。
- 不依赖颜色单独表达状态。
- 不使用超过8px的静态面板圆角。
- 不把数据源配置与云函数配置混在同一个长表单中。
