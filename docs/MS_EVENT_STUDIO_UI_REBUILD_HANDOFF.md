# MS Event Studio Phase 2R UI 重构交接

最后更新：2026-08-13（Asia/Shanghai）

科学与打包代码基线：`a587b37`（`0.2.0.dev3`）

当前结论：**科学核心、持久化、导出和 Windows 打包链可作为回归基线；dev3 的 UX
验收失败，不是可交付候选。**

本文是下一阶段桌面端工作的唯一主交接。开始实现前还必须阅读：

- [`../AGENTS.md`](../AGENTS.md)
- [`scientific_contract.md`](scientific_contract.md)
- [`project_and_export_contracts.md`](project_and_export_contracts.md)
- [`phase2_desktop_and_uat.md`](phase2_desktop_and_uat.md)

`../lma-studio` 固定在 v0.4.4，只能作为只读参考，不能修改，也不能成为运行时依赖。

## 1. 已确认的产品决定

这些决定来自用户反馈和三项独立审查，不需要在新 Session 中重新讨论：

1. MS Event Studio 与 LMA Studio 是同一产品系列。应用壳、字体层级、间距、圆角、
   阴影、按钮、卡片、模态框和反馈方式应保持一致；区别来自产品名、MS 峰形图标、
   青色信号轨迹和 MS 审阅任务，而不是另造一套 UI 语言。
2. 前端中文优先，可保留必要且简洁的科学英文，例如 PC34、MS782、TIC、m/z、ppm。
   EventID、revision、SQLite、snapshot、bucket、manifest、machine contract 等实现术语
   不出现在日常界面。
3. 不再继续修补现有 `ttk` 审阅页。目标壳迁移到与 LMA 相同的
   **pywebview + HTML/CSS/SVG** 技术；现有 Python 科学核心和项目服务继续复用。
4. macOS ARM64 仍按 LMA 的方式走 GitHub Actions 原生 `macos-14` 构建；Windows x64
   走 `windows-2022`。PyInstaller 不做跨平台伪构建。
5. 用户只承担最终科研语义与主观体验验收，不再承担第一轮缺陷发现。候选交给用户
   前，必须完成自动化、标准状态截图和三个独立代理的 pre-UAT。
6. `0.2.0.dev3` 保留为科学/持久化/打包回归基线，不作为视觉或交互基线。下一份真正
   的 WebView 候选建议升为 `0.3.0.dev1` / `0.3.0-dev1`。

## 2. 第一性原理：软件首先要完成什么

一个科研审阅者打开项目后，核心工作只有四件事：

1. 看清当前信号与被选中的事件；
2. 用最少的认知负担判断“这个事件是否保留”；
3. 只有遇到异常时，添加遗漏峰或重新定位峰顶；
4. 在项目级别修改范围或导出结果，并清楚知道会产生什么影响。

因此 UI 必须遵守以下约束：

- 一项操作只表达一个意图；位置恢复不能暗中改变审阅状态。
- 进入编辑模式后，模式、允许区域、候选结果、提交和取消必须同时可见。
- 任何写入前都要有明确目标；点击图例、坐标轴或绘图区外绝不写入。
- 正常审阅一屏完成；高级证据折叠，项目级操作离开事件检查器。
- 选中态、禁用态、保存中、成功、失败和回滚都必须可见，不能只写在底部状态栏。
- 科学保护仍然存在，但用用户能理解的语言和单一流程表达。

## 3. 为什么必须更换 UI 壳

当前欢迎页为了圆角使用 Canvas 自绘，审阅页仍是 `ttk/clam`。LMA 使用
WebView2/DirectWrite 或 Cocoa/WebKit 加 CSS。两者在字体抗锯齿、line-height、圆角、
阴影、焦点环、布局和缩放行为上没有同一套语义。继续调整 Tk 字体和 padding 只能
得到近似外观，还会形成一套难以维护的自制控件库。

迁移只替换表现层，不重写科学程序：

```text
HTML/CSS/SVG workbench
        │ local loopback API + narrow native dialog bridge
        ▼
Web session/view-model adapter
        │
        ├─ parser.py / detector.py / display.py
        ├─ project.py / window_service.py
        ├─ review.py / reconcile.py
        ├─ range_change.py
        └─ export.py
```

建议新增的代码边界：

```text
src/ms_event_studio/
  web_desktop.py          # pywebview 生命周期、原生路径对话框、运行时检查
  web_app.py              # loopback server、API、任务/session 状态
  web_models.py           # 只面向 UI 的结构化 view model
  web/
    index.html
    app.css
    app.js
    icons/*.svg
```

`desktop_bundle/ms_event_studio_gui.py` 最终指向 `web_desktop.main`。迁移中可暂时保留
`desktop.py` 供回归对照，但不再向其中增加 UI 功能；完整工作台通过验收后删除或明确
归档旧 Tk 入口，正式包中只保留一个渲染栈。

本仓库应复制并适配 LMA 的实现模式，不能从 `../lma-studio` 动态导入。优先只读参考：

- `lma-studio/annotation_app/desktop.py`：本地服务、pywebview 生命周期、路径对话框、
  capability URL 和窄桥接；
- `lma-studio/annotation_app/app.py`：约 11535 行后的基础 tokens，约 11763 行的
  segmented control，约 11954 行的 key/value 证据，约 12006 行的 interaction hint，
  约 12552 行的欢迎页；
- `lma-studio/packaging/windows/` 与 `packaging/macos/`：pywebview 6.2.1、PyInstaller
  hidden imports/data、原生引擎和平台构建。

## 4. 目标信息架构

### 4.1 应用壳

- 64 px 深色顶栏：产品标识、当前项目名、范围摘要；右侧放“新建/打开”“项目设置”
  “导出结果”。
- 工作区采用 LMA 的两栏骨架：左侧 280–320 px 任务栏，右侧自适应主图。
- 不再在右侧使用一个大 `Text` 和多个 `LabelFrame` 堆叠信息。
- 底部只显示保存、失败、后台任务进度；不放操作说明和后端实现术语。

### 4.2 左侧任务栏

从上到下固定为：

1. 审阅进度与上一/下一事件；打开项目时自动选中第一个未审阅事件。
2. 当前事件标题：大号峰顶时间、状态 badge、自动/人工来源。
3. “保留这个事件吗？”互斥分段控件：**保留 / 排除 / 待定**。当前选择必须明显；
   “清除审阅”是低优先级文本操作，不与三项主决策并排。
4. 核心证据：PC34 强度、实测 m/z、质量误差和质量结论。
5. “更多证据”折叠区：扫描编号、MS782、TIC、显著度、物理峰宽、可调整区间。
6. 条件操作：选中自动事件且峰顶被改过时才显示“恢复自动峰顶”。
7. 操作备注，清楚说明其用途；输入框获得焦点时单字母快捷键全部停用。

审阅成功后默认移动到下一个未审阅事件，并在顶部短暂显示保存成功；如果写入失败，
原地恢复准确的旧状态并展示可操作错误。

### 4.3 主图与工具栏

主图上方只放查看与绘图相关操作：窗口起点/宽度、前后窗口、事件筛选、线性/对数、
标签开关、撤销/重做和“添加遗漏峰”。修改项目范围和导出不放在这里。

SVG 主图必须满足：

- 数据域至少预留 8–12% 顶部 headroom；marker、label、legend 的 bbox 距内容边界至少
  4 px，并由 clip path 限制，不允许最高峰的三角形或字母越过灰线。
- 默认用形状与颜色表达 U/A/R/P，不在每个峰顶重复绘制 `U/A/R/P` 字母；选中或悬停
  时再显示带白色描边的 callout。
- 事件层与降采样信号层分离，自动峰顶不能被 envelope 降采样吃掉。
- 绘图区外、图例、坐标轴、空白边距均不是写入 hit target。
- 高密度标签有碰撞避免；透明 hit area 提升选择容错，但不得改变科学定位。

### 4.4 编辑模式

“添加遗漏峰”和“重新定位峰顶”采用显式状态机，而不是点击按钮后只改变鼠标：

```text
selected
  ├─ add-aim ──hover/click──> add-preview ──apply──> saving ──> selected
  └─ adjust-aim ────────────> adjust-preview ─────> saving ──> selected

任意 aim/preview --Esc 或取消--> selected
```

进入模式后必须同时出现：

- 保持选中的模式按钮；
- 图内就近操作条；
- 添加模式的候选扫描，或调整模式的“可调整区间”色带；
- 悬停吸附预览；
- before → after 数值；
- “应用”和“取消”。

点击只产生预览，第二步“应用”才写入。后台最终提交时重新校验真实扫描、revision 和
允许区间，不能信任浏览器坐标。

### 4.5 明确拆开的领域动作

| 用户动作 | 精确效果 |
|---|---|
| 清除审阅 | 状态回到未审阅；峰顶位置不变 |
| 恢复自动峰顶 | 只恢复自动检测的原峰顶；当前审阅状态不变 |
| 撤销添加 | 通过审计历史撤销最近一次添加；不伪装成“恢复原始” |

当前 `_restore_patch()` 会把位置恢复和状态变化耦合。Phase 2R 必须在服务层拆出上述
语义，并为每种动作增加审计、撤销/重做、重开持久化和导出测试，不能只在前端改文案。

### 4.6 范围与导出

修改范围只使用一个模态流程：当前范围、起点、终点、输入错误、影响预览和最终应用
都在同一个 8 px 圆角窗口内。预览对象保留在服务端并绑定一次性 token；应用必须使用
同一个 `RangeChangePreview` 及其状态保护，不能在客户端或确认时静默重算。

导出文案改为用户目标：

- 主操作：**导出审阅结果…**，默认仅导出已保留事件，可选择包含待定，并预先显示
  当前范围、状态过滤和预计行数；
- 高级操作：**导出完整审计数据包…**，说明用于复核、归档或下游程序读取；
- 禁止出现“人用 CSV”“machine contract”等开发者分类。

## 5. 与 LMA 共享的视觉规则

先抽成一个 MS 仓库内的 token 文件，不允许各组件散落字号和颜色：

| 层级 | 规则 |
|---|---|
| 正文 | `14px/1.45 Arial, Helvetica, sans-serif`，由同一 WebView 字体回退渲染中文 |
| 顶栏标题 | 17 px / 700 |
| 欢迎页标题 | 20 px / 1.25 |
| 模态标题 | 16 px / 700 |
| 区块标题 | 13 px / 700 |
| 次级文字 | 12 px；紧凑/图表标签 10–11 px |
| 背景/卡片 | `#f6f7f9` / `#ffffff` |
| 主文字/次文字 | `#1b1f27` / `#667085` |
| 边框 | `#d7dce3`，1 px |
| 卡片 | 8 px 圆角，`0 10px 26px rgba(20,26,36,.08)` |
| 控件 | 高 34 px，横向 padding 10 px，6 px 圆角 |
| 语义色 | success `#067647`，danger `#b42318`，warning `#b54708` |
| 工作区 | 14 px padding / 14 px gap |

复用 LMA 的 app header、panel/card、segmented、key/value、modal、tooltip、toast、
focus ring 和 interaction hint。状态色只用于 badge、选中态和图上标记，不把三个高饱和
大按钮长期铺满侧栏。

## 6. Web 服务与安全边界

- 使用进程随机 loopback 端口和 capability URL；普通浏览器响应保持严格 CSP。
- pywebview 的 Python bridge 只暴露必要的原生文件/目录选择；所有项目与审阅操作走
  同源 API。Python 再次验证路径、事件 ID、revision、范围和状态枚举。
- 大文件分析采用任务 ID、进度轮询和取消；不能阻塞 UI 线程，也不能把原始路径写入
  浏览器持久存储。
- 结构化 UI view model 不返回原始数据库行。正常页面不返回内部 schema、EventID、
  revision 或文件系统实现细节；提交时所需 opaque token 可存在内存状态中。
- 状态写入继续使用现有 optimistic revision、单写者、失败回滚和 append-only audit。
- 原始 MS 文件始终只读；项目创建与范围切换继续使用现有 staging/atomic switch。

建议 API 能力，而非固定 URL 命名：

- bootstrap、新建/打开项目、源检查任务及取消；
- 当前项目摘要、窗口数据、事件导航和结构化证据；
- 审阅决策与清除决策；
- 添加/调整的 preview 与 apply；
- 恢复自动峰顶、撤销、重做；
- 带服务端 token 的范围 preview/apply；
- 审阅结果和完整审计数据包导出。

## 7. Windows 与 macOS 打包决定

当前 dev3 使用 Tk，不加载 CLR，因此同目录没有 `.exe.config` 是正确的。迁移到
pywebview 后情况改变：Windows 的 WebView2/pythonnet/CLR 打包必须像 LMA 一样在
`MS-Event-Studio.exe` 同目录放置 `MS-Event-Studio.exe.config`，其中启用
`loadFromRemoteSources`。新构建脚本必须复制它，并用测试断言文件存在且内容正确。

Phase 2R 打包还必须：

- 将 `pywebview==6.2.1` 固定在 Windows/macOS 原生构建依赖；
- 在 PyInstaller spec 中收集 webview 的 `lib/js` data、平台动态库和正确 hidden imports；
- Windows 只带 Edge Chromium/WinForms 所需后端，并检查 WebView2 Runtime；
- macOS 只带 Cocoa/WebKit 所需后端，GitHub Actions 在 `macos-14` ARM64 原生构建；
- 源码运行、冻结包和 CI 都打包 HTML/CSS/JS/SVG 资源；
- packaged smoke 不只 import 模块，还要启动隐藏 WebView、加载首页、调用核心 API、
  完成 Parquet/SQLite/导出回环；
- macOS 继续做 `codesign`、`plutil` 和 `file` 检查；Developer ID/notarization 仍是正式
  发布阶段，不是假装已经完成的 Phase 2R 条件。

## 8. 已知缺陷与对应自动门禁

| 当前缺陷 | 根因 | Phase 2R 必须新增的门禁 |
|---|---|---|
| 最高峰 `U` 三角越过上边框 | 最大值映射到 plot top，marker/文字继续向上画 | marker/label/legend bbox 的尺寸×DPI×状态参数化测试 |
| 点击图例或坐标轴也可能提交调峰 | `_canvas_click` 不验证 y 和 plot rectangle | 图内/图外 hit-test 集成测试，图外零写入 |
| 调峰模式不可发现 | `set_mode` 只改 cursor 和底部文字 | active 按钮、模式条、区间、preview、Esc 测试 |
| 证据是一整块文字 | disabled Tk `Text` 拼接所有字段 | DOM 必须有语义组和 key/value；核心证据无需滚动 |
| 状态按钮没有当前态 | 三个高饱和按钮无 selected 语义 | segmented 的 aria/active/disabled/saving 测试 |
| 修改范围跳多个窗口 | 两次 `askstring` 再 `askyesno` | 单 modal 的 input→preview→apply 状态测试 |
| 后端术语暴露 | 状态栏和导出文案直接复用实现名 | UI 文案 lint 与 DOM 快照 |
| 现有 84 项测试未构造真实审阅 UI | 主要覆盖 headless 模型、DPI API 和图标尺寸 | Playwright DOM/交互/截图 + 原生包截图 |

UI 文案 lint 至少禁止日常页面出现：`人用`、`machine contract`、`SQLite`、`snapshot`、
`快照`、`bucket`、`分桶`、`EventID`、`revision`、`manifest`、`stale`、
`immutable support`。完整审计数据包的高级说明允许用中文解释版本化和校验，但仍不
直接暴露数据库术语。

## 9. 标准验收矩阵

### 9.1 自动化

1. 现有 84 项科学、项目、审阅、范围、导出和路径安全测试保持通过。
2. 新增纯 view-model/API contract 测试，覆盖成功、冲突、取消、失败回滚和重开。
3. Playwright 覆盖鼠标、键盘、焦点、disabled、loading、Esc、preview/apply 和失败注入。
4. 几何测试覆盖所有 marker 形状、状态、最高/边缘/密集峰、窗口尺寸与缩放。
5. 截图测试覆盖：
   - 欢迎页；新建项目空闲、分析中、取消、错误、可创建；
   - 无选择；U/A/R/P；自动/人工；保存中与保存失败；
   - 添加 aim/preview；调整 aim/preview；超范围；
   - 空/可用撤销重做；范围输入/预览；两种导出；
   - 最高峰、边缘峰、密集峰和长中文；
   - 960×640、1366×768、1920×1080，浏览器基线；
   - Windows 100/125/150/200% 原生 DPI 与 macOS 原生 Retina 抽样。
6. 完整包 smoke 通过后，再用只读 `HSC1_data/Lin-_MPP.txt`（约 7.38 GiB）做真实文件
   端到端；项目必须写到独立 UAT 目录，保存前后 size/mtime/SHA-256 不变。

### 9.2 可用性硬门禁

- 首次选中事件后，核心状态、峰顶时间、PC34、m/z/ppm 与质量结论在 1920×1080、
  Windows 150% 下无需滚动。
- 一次审阅决策最多两个主交互；成功反馈与自动前进目标小于 250 ms。
- 不读说明也能完成：选择事件 → 保留/排除 → 重新定位 → 取消。
- 调整模式中 active 按钮、就近说明、可调整区间和候选预览同时可见。
- 点击绘图区外、取消或 Esc 均为零写入；失败后视图与数据库一致。
- 修改范围从输入到影响预览和确认始终只有一个 modal；取消为零写入。
- 所有 marker 与 label 距 plot 内容边界至少 4 px。
- 100–200% DPI 与规定 viewport 下无模糊缩放、文字裁切、按钮重叠或横向溢出。
- Windows 与 LMA 同 DPI 并排截图能被识别为同一产品家族，但 MS 信号图和品牌可区分。

### 9.3 独立代理 pre-UAT

每个可交付候选由主代理发起三项互不代替的只读审查：

1. **任务/交互代理**：不读操作文档，完成审阅、添加、调整、取消、范围和导出任务；
2. **LMA 视觉一致性代理**：与冻结 LMA 在相同窗口和 DPI 下逐状态并排审查；
3. **QA/可访问性代理**：检查键盘、焦点、对比度、缩放、边界、错误、回滚和截图矩阵。

三者的阻断和高严重度问题全部关闭、自动门禁全绿、原生包 smoke 通过后，才把候选与
一份精简 UAT 指南交给用户。用户不应再负责报告明显的布局、字号、越界或按钮语义问题。

## 10. 执行阶段

### UX-R0：冻结边界并建立迁移护栏

- 确认工作树、记录 `a587b37` 科学代码基线并跑全量现有测试。
- 三个代理分别复核 LMA 复用点、API/科学边界、QA/打包边界。
- 建立 WebView 目录骨架、设计 token 单一来源和旧 Tk 不再扩展的代码注释/测试护栏。
- 将 dev3 截图仅作为失败对照，不作为 golden。

退出条件：科学测试不退化，目标文件图、API 边界和截图矩阵在仓库内可执行。

### UX-R1：WebView 应用壳与欢迎/新建项目

- 按 LMA 实现 loopback server、pywebview host、原生路径选择和共享 tokens。
- 重建欢迎页、新建/打开项目与源分析进度/取消。
- 同步 Windows `.exe.config`、pywebview 依赖和两平台 spec 的最小可启动路径。

退出条件：源码与隐藏 WebView smoke 可启动；欢迎/新建项目截图与 LMA 同系列；不存在
Tk/WebView 混合页面。

### UX-R2：结构化 API 与项目工作台骨架

- 将窗口、事件、证据、进度和历史能力转为结构化 view models。
- 实现左任务栏 + 右 SVG 主图、窗口浏览、筛选、线性/对数和选择。
- 保持 min/max envelope 与事件 overlay 的科学行为。

退出条件：真实项目只通过服务层浏览；核心证据有语义分组；最高峰与密集峰几何测试通过。

### UX-R3：审阅决策闭环

- 实现分段状态控件、清除审阅、备注、保存中、失败回滚、自动前进、键盘与焦点规则。
- 拆开“恢复自动峰顶”和“清除审阅状态”的后端语义。

退出条件：U/A/R/P、并发冲突、撤销/重做和重开持久化的 API/UI 测试全绿。

### UX-R4：异常峰编辑

- 实现添加遗漏峰、重新定位峰顶的 aim→preview→apply 状态机。
- 高亮允许区间、候选吸附、before→after、取消/Esc 和图外零写入。

退出条件：代理不读说明完成两项任务；边界与失败注入测试全绿。

### UX-R5：范围与导出

- 单 modal 范围输入/服务端 token 预览/原子应用。
- “导出审阅结果”和“导出完整审计数据包”模态流程。

退出条件：取消零写入，旧科学/导出 contract 全绿，正常 UI 无禁用术语。

### UX-R6：中文文案、响应式、可访问性与引导

- 完成中文层级、tooltip、focus ring、aria、长文本、错误和空状态。
- 引导测试改为可折叠的情境清单或 coachmark，不再永久占据蓝色横条。
- 跑完整浏览器截图矩阵和三代理 pre-UAT，修完所有阻断/高问题。

退出条件：所有自动与代理门禁通过；此时才允许生成给用户的精简测试指南。

### UX-R7：原生 Windows/macOS 候选

- Windows 本机构建并验证 WebView2、`.exe.config`、图标、DPI、packaged smoke 和截图。
- GitHub Actions `macos-14` 构建 ARM64 `.app`，完成包结构/签名检查与 macOS 真机 UAT。
- 产出 ZIP、sidecar、bundle tree hash、smoke payload 和审查报告。

退出条件：两平台原生候选与全部证据齐备；仍不使用稳定 tag。

### UX-R8：移除遗留 UI 并形成新候选

- 删除或归档不再使用的 Tk UI 和 Tk/Tcl 打包依赖，正式入口只剩 WebView。
- 更新版本、README、构建文档、用户指南和候选 hash。
- 将 `0.3.0.dev1` 明确标为通过 pre-UAT 的候选，再交给用户最终验收。

退出条件：文档与代码一致、Git 工作树干净、两平台证据可追溯；用户验收通过后才宣布
Phase 2R exit。

## 11. 不在本轮范围内

- 不修改 PC34 检测算法、冻结阈值、ppm 窗口或真实扫描吸附规则。
- 不导入 LIF、UMAP、细胞标签、预期事件数或 LMA 项目状态。
- 不实现 Phase 3 的 LMA 导入，也不覆盖 LMA 的 `ms_events.parquet`。
- 不在 UX-R0/R1 顺便重构科学核心。
- 不把真实数据或 UAT 项目提交到 Git。
- 不把 dev3 的 Tk 截图美化后冒充新架构验收。

## 12. 当前证据与文档角色

- `phase2_desktop_and_uat.md`：dev3 科学/性能/打包历史证据，不是当前 UX 验收结论。
- `guided_test_zh.md`：dev3 遗留交互回归步骤；Phase 2R 候选形成后必须重写。
- `github_actions_builds.md`：当前 native-runner 政策；WebView 迁移后需同步依赖和 smoke。
- `dist/`、`release/`：忽略的本地产物，可用于回归，但不构成当前源代码的发布证明。

下一 Session 的可复制开头语见
[`MS_EVENT_STUDIO_UI_REBUILD_NEXT_SESSION.md`](MS_EVENT_STUDIO_UI_REBUILD_NEXT_SESSION.md)。
