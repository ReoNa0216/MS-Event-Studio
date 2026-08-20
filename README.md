# MS Event Studio

MS Event Studio 是一个独立的 MS-only 事件提取、审阅与导出工具。它只处理质谱信号，
不会导入 LIF、UMAP 坐标、细胞标签、预期事件数量或 LMA Studio 项目状态；原始 MS
文件在桌面流程中始终只读。

## 当前公开测试状态

当前公开预发布版本为 `0.4.1`。桌面界面使用单一的 pywebview + HTML/CSS/SVG 渲染器，
包含从新建项目到审阅、事件编辑、范围调整和导出的完整用户流程。Windows x64 与
macOS Apple Silicon 构建会发布在本仓库的
[Releases](https://github.com/ReoNa0216/MS-Event-Studio/releases) 页面。

已经完成的 pre-UAT 证据包括：

- 完整 Python 与前端单元测试、浏览器交互门禁和打包隐藏冒烟；
- 960×640、1366×768、1920×1080 的 36 个确定性场景截图；
- Windows 原生 100%、125%、150% 与 200% 缩放下的任务、DPI、焦点和视觉检查；
- 科学/API 边界、交互与可访问性、LMA v0.4.4 视觉一致性的独立审查；
- 大型只读 MS 源的创建、审阅、重开和两类导出整链验证。

Windows 主流程已经过用户人工验收。`0.4.1` 在此基础上补齐原始/实时两类近邻风险的独立显示和批量逻辑或门禁；`0.4.0` 已新增跨平台导出修复、当前窗口批量
事件批量保留、当前事件独立高亮、时间刻度、项目级 marker/相邻阈值设置和启动优化。
macOS ARM64 包由 GitHub Actions 原生构建并通过 Cocoa 隐藏启动与科学冒烟；Retina 可见界面
和真实鼠标体验仍需要 Apple Silicon 用户反馈。

`0.4.0` 使用新的 `ms-event-project-v2` 项目格式。旧公开测试项目请从原始 MS 文件重新创建，
不会进行可能误解 marker 身份的静默迁移。

## 主要能力

- 一次读取源文件，显示字节与扫描进度，支持取消，并复用检查结果创建原子项目；
- 新建项目时可设置主 marker m/z 和“相邻事件距离较近”的提示阈值；核心质量窗口固定为 ±12 ppm。
  默认 `760.5851` 已有真实数据回归，替代 marker 目前只有技术链路和合成提取验证，不能视为
  已证明适用于任意离子；
- 在 SVG 信号图中查看主 marker 信号、当前事件高亮、底部时间刻度和稳定的透明数字时间标签；
- 显示所选事件的核心与更多物理证据，并以保留、排除、待定或未审阅记录结论；
- 保存操作备注，支持恢复自动峰顶、撤销、重做以及关闭后重开；
- 仅在自动标记偏离真实局部峰顶时，放大实际曲线并通过瞄准、预览和应用重新定位；
- 先预览影响，再安全应用分析范围变化；
- 一次保留当前窗口内未发现与相邻事件距离过近的未审阅事件；分别按自动识别时的位置和
  当前峰顶判断，任一位置距离过近就留给人工逐个处理，整批可作为一个操作撤销或重做；
- 导出默认仅含已保留事件的审阅结果，可选择包含待定事件；审计数据包只需选择保存位置，
  应用会创建最终文件夹。

界面沿用冻结的 LMA Studio v0.4.4 视觉语言，但不修改、不导入也不运行时依赖 LMA
Studio。LMA v0.4.5 以后用于“外部事件坐标名单”的辅助通道不属于本产品当前输入合同；
若未来确有使用场景，必须单独设计和验证，不能混入核心自动检测。不要用任何导出覆盖
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

- [当前产品状态与未完成事项](docs/product_status.md)
- [科学规则](docs/scientific_contract.md)
- [marker m/z 的数学原理与验证边界](docs/marker_mz_principles_zh.md)
- [项目与导出合同](docs/project_and_export_contracts.md)
- [桌面构建与公开测试](docs/github_actions_builds.md)
