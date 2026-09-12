# MS Event Studio

项目内新增“打包分享项目”，自动排除 macOS 系统元数据；MS→LMA 正式交换入口统一为“LMA 事件包”。操作与保存边界见[项目分享](docs/project_sharing.md)。
`v0.6.0` 已接入 HRGC：打开项目 → **Feature 提取** → 手动排除 QC 时间段（可选）→ 开始提取。自动定位创建项目时使用的原始 MS 文件；仅缺失时重新定位。结果随项目保存。**导出结果 → 导出分析结果** 生成一个 ZIP，包含事件 CSV 和勾选的当前矩阵（H5AD）；已有矩阵导出不依赖原始 MS 文件。**传给 LMA Studio** 生成独立交接 ZIP，保留完整 v2 事件并可附带矩阵；项目分享移至 **新建 / 打开 → 分享当前项目…**。与 LMA Studio v0.7.0 配合使用。

构建使用 `packaging/computation.json` 固定两个计算包的源码 commit；运行 `python scripts/resolve_computation.py` 后安装本产品。CI 优先使用 `FLAME_MS_CORE_011_WHEEL_BASE64` 与 `FLAME_FEATURE_CORE_WHEEL_BASE64` 的哈希锁定安装包，也支持具有私有仓库读取权限的 `FLAME_COMPUTATION_READ_TOKEN`，详见[构建说明](docs/github_actions_builds.md)。


MS Event Studio 是一个独立的 MS-only 事件提取、审阅与导出工具。它只处理质谱信号，
不会导入 LIF、UMAP 坐标、细胞标签、预期事件数量或 LMA Studio 项目状态；原始 MS
文件在桌面流程中始终只读。

## 当前正式版本

当前正式版本为 [v0.6.0](https://github.com/ReoNa0216/MS-Event-Studio/releases/tag/v0.6.0)，提供 Windows x64 与 macOS Apple Silicon 包及 SHA-256 校验文件。

解析与 calling 使用独立的 `flame-ms-core==0.1.1`；“LMA 事件包”采用 v2 格式，可保真导入 LMA Studio v0.7.0。新增“打包分享项目”，用于将完整项目交给同事继续审阅。见 [接入与构建说明](docs/flame_task1.md)和[版本说明](README_RELEASE.md)。

本版本发布前已有完整自动测试、108 张标准浏览器截图矩阵、三个独立工程/UI/QA 审查，以及真实项目 ZIP 解压重开和逐表/逐文件核对。两个平台均由同一标签构建，并在发布前执行各自的测试与打包隐藏启动/科学冒烟。

v0.4.1 的 Windows 人工验收是历史证据；v0.5.0 的实际标注验收由课题组成员继续完成。macOS 隐藏启动不代表 Retina 可见界面和真实鼠标体验已经人工验收。

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
- “导出结果”按用途提供分析 ZIP 和 LMA 交接 ZIP，均可附当前矩阵；项目分享位于“新建 / 打开”。只需选择已有父目录，无需预建空文件夹。分析 CSV 默认仅含已保留事件，待定选项不改变矩阵行数。

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

Windows 包采用 `onedir` 形式。解压后必须保留整个 `MS-Event-Studio` 文件夹，
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

v0.4.1 的 Windows 人工验收已通过；本版交由课题组成员实际标注验收。[Windows 快速复测操作卡](docs/guided_test_zh.md) 保留作后续
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
不能替代原生证据。Windows 四档原生证据与旧版人工验收保留作历史参照；当前候选仍待
Windows 联合人工验收，macOS Retina 真机验收随后安排。

进一步资料：

- [当前产品状态与未完成事项](docs/product_status.md)
- [科学规则](docs/scientific_contract.md)
- [marker m/z 的数学原理与验证边界](docs/marker_mz_principles_zh.md)
- [项目与导出合同](docs/project_and_export_contracts.md)
- [桌面构建与公开测试](docs/github_actions_builds.md)
