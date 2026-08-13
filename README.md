# MS Event Studio

MS Event Studio 是一个独立的 MS-only 事件提取、审阅与导出工具。它只处理质谱信号，
不会导入 LIF、UMAP 坐标、细胞标签、预期事件数量或 LMA Studio 项目状态；原始 MS
文件在桌面流程中始终只读。

## 当前候选状态

当前 WebView 候选版本为 `0.3.0.dev1`。桌面界面已经迁移为单一的
pywebview + HTML/CSS/SVG 渲染器，不会回退到旧 Tk 审阅页。候选包含从新建项目到
审阅、事件编辑、范围调整和导出的完整用户流程。

已经完成的 pre-UAT 证据包括：

- 完整 Python 与前端单元测试、浏览器交互门禁和打包隐藏冒烟；
- 960×640、1366×768、1920×1080 的 36 个确定性场景截图；
- Windows 原生 100%、125%、150% 与 200% 缩放下的任务、DPI、焦点和视觉检查；
- 科学/API 边界、交互与可访问性、LMA v0.4.4 视觉一致性的独立审查；
- 大型只读 MS 源的创建、审阅、重开和两类导出整链验证。

Windows 最终候选已于 2026-08-13 通过用户人工验收。随后从已验收源码重新生成了
macOS ARM64 候选；它已通过 GitHub Actions 原生构建、Cocoa 隐藏启动、签名和科学冒烟，
并发布到公开的
[`MS-Event-Studio-Releases`](https://github.com/ReoNa0216/MS-Event-Studio-Releases/releases/tag/v0.3.0-test1)
仓库；同一发布页同时提供已通过用户验收的 Windows x64 包和供 Apple Silicon 用户测试的
macOS ARM64 包。下一步只剩 Retina 可见界面与真实鼠标 UAT；完成前仍不宣布 Phase 2R
退出。`0.2.0.dev3` 仅作为冻结的科学回归基线，不再是 UX 候选。

## 主要能力

- 一次读取源文件，显示字节与扫描进度，支持取消，并复用检查结果创建原子项目；
- 在 SVG 信号图中查看 PC34 信号、轻量事件标记和稳定的透明数字时间标签；
- 显示所选事件的核心与更多物理证据，并以保留、排除、待定或未审阅记录结论；
- 保存操作备注，支持恢复自动峰顶、撤销、重做以及关闭后重开；
- 仅在自动标记偏离真实局部峰顶时，放大实际曲线并通过瞄准、预览和应用重新定位；
- 先预览影响，再安全应用分析范围变化；
- 导出默认仅含已保留事件的审阅结果，可选择包含待定事件，或导出完整审计数据包。

界面沿用冻结的 LMA Studio v0.4.4 视觉语言，但不修改、不导入也不运行时依赖 LMA
Studio。正式 LMA 导入属于 Phase 3，必须走单独验证的合同路径。不要用任何导出覆盖
LMA Studio 的 `ms_events.parquet`。

## 运行

从源码启动：

```powershell
python -m pip install -e ".[packaging]"
ms-event-studio-gui
```

Windows 候选采用 `onedir` 形式。解压后必须保留整个 `MS-Event-Studio` 文件夹，
从文件夹内运行 `MS-Event-Studio.exe`；单独复制 EXE 无法运行。正式验收只使用交付记录
中版本和 SHA-256 完全匹配的候选包，不要把 `dist/` 中的中间构建当作交付包。

命令行仍可独立使用：

```powershell
ms-event-studio create --source "D:\data\run.txt" --project "D:\projects\run" `
  --name "Run" --start-min 10 --end-min 60
ms-event-studio verify --project "D:\projects\run"
ms-event-studio export --project "D:\projects\run" --output accepted.csv
ms-event-studio export-machine --project "D:\projects\run" --output-dir audit-package
```

## 用户验收

Windows 人工验收已经通过；[Windows 快速复测操作卡](docs/guided_test_zh.md) 保留作后续
回归使用。它只包含普通 Windows 用户需要点击和观察的科研任务，不要求重复自动化或
工程级边界测试。

用户只需说明 Windows 版本与显示缩放、点了什么、看到了什么，并附一张完整窗口截图；
候选版本和 SHA-256 由维护者从当前交付记录中补齐。若问题发生在写入操作之后，再说明
界面是否提示保存完成，以及关闭重开后的状态。

## 开发验证

```powershell
$env:PYTHONPATH = "src;tests;."
python -m unittest discover -s tests -v
npm --prefix src/ms_event_studio/web test
python scripts/lint_ui_copy.py
python scripts/capture_ui_matrix.py --validate-only --require-all
```

真实数据回归和原生截图必须在受控资产与对应真实平台上运行；浏览器缩放或响应式代理
不能替代原生证据。Windows 四档自动证据和用户人工验收均已完成；当前剩余的人工作业是
macOS Retina 真机验收。

进一步资料：

- [科学规则](docs/scientific_contract.md)
- [项目与导出合同](docs/project_and_export_contracts.md)
- [Phase 1 真实数据回归](docs/phase1_real_regression_summary.md)
- [历史 dev3 桌面、性能与打包证据](docs/phase2_desktop_and_uat.md)
- [Phase 2R WebView 重建交接](docs/MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md)
