# GitHub Actions 桌面构建与公开测试

MS Event Studio 只使用一个公开仓库：
[`ReoNa0216/MS-Event-Studio`](https://github.com/ReoNa0216/MS-Event-Studio)。源码、问题反馈、
Windows x64 与 macOS Apple Silicon 下载都在同一仓库；不再维护单独的 Releases 仓库。

## 日常候选构建

在 GitHub 打开 **Actions → Build and release desktop packages → Run workflow**：

1. `platform` 选 `all`，确保 Windows 与 macOS 来自同一个提交；
2. `version` 填应用版本，开发构建例如 `0.6.0.dev1`，正式发布使用已确认的稳定版本；
3. 首次审计保持 `publish_prerelease` 关闭，只下载 Actions artifacts 检查；
4. 两个平台构建和隐藏启动均通过后，再以相同提交运行并打开
   `publish_prerelease`，供真实用户下载测试。

手动候选的 GitHub 标签为 `candidate-<version>`，但软件内部版本和 ZIP 文件名仍是干净的
`<version>`。正式稳定版使用 `v<version>` Git 标签；工作流会移除标签前缀 `v` 后再生成
ZIP，因此文件名不会出现多余的 `v`。

## 固定计算源码

Python 3.11 构建使用 `packaging/computation.json` 固定 `flame-ms-core` 与 `flame-feature-core` 的 commit 和包版本。`scripts/resolve_computation.py` 从干净 Git archive 安装；原 HRGC 源码不复制进产品仓库。Numba 源文件、LLVM、HDF5 和依赖版本元数据进入冻结包，隐藏科学冒烟实际启动子进程提取并重开 H5AD。

Actions 优先使用 `FLAME_MS_CORE_011_WHEEL_BASE64` 和 `FLAME_FEATURE_CORE_WHEEL_BASE64` 中的固定 wheel，安装前严格校验 `packaging/computation.json` 的 SHA256。两份 wheel 从同一清单的精确 commit 干净 archive 构建，未改变计算源码；secret 保存的是安装包，不是账号 token。未配置 wheel 时仍可用 `FLAME_COMPUTATION_READ_TOKEN`（两个私有计算仓库的 contents:read）取得源码。旧 0.1.0 的 `FLAME_MS_CORE_WHEEL_BASE64` 不适用于当前 0.1.1。构建脚本失败时不要跳过依赖校验。

## 每个平台实际证明什么

- Windows 由 `windows-2022` 构建 x64 onedir 包，强制 Edge Chromium/WebView2，拒绝
  Tk/Tcl、Android、旧 MSHTML 和非 x64 Loader。
- macOS 由 `macos-14` 的 ARM64 Python 构建 `.app`，强制 Cocoa，执行隐藏 WebView/API/
  科学冒烟，ad-hoc 签名后再次验证并刷新最终清单。
- 两边都先运行完整 Python 测试与截图矩阵结构检查，再生成 ZIP 和 SHA-256 sidecar。
- Actions 的 macOS 隐藏启动不能替代 Apple Silicon 真机上的 Retina 可见界面和鼠标体验；
  v0.5.0 已由用户明确授权直接发布正式版；人工可见验收仍单独记录，由课题组完成。

## 本地成品目录

`dist/` 只保留当前平台的最终候选：Windows 为 `dist/windows`，macOS runner 为
`dist/macos`。中间构建、截图和诊断证据放在 ignored 的 `build/`，发布 ZIP 放在
`release/`。不要再创建 `dist/windows-ux-*` 之类的临时目录。

## 版本 0.4.0

`0.4.0` 是新的 `ms-event-project-v2` 测试版。它增加项目级主 marker、相邻事件提示阈值、
当前窗口无相邻提示事件批量保留、跨平台导出修复和启动优化。旧测试项目必须从只读 MS 原始文件
重新创建，不做可能错误解释科学列的兼容迁移。

## 版本 0.4.1

`0.4.1` 在不改变 marker、±12 ppm、检测器和 `0.60 s` 默认阈值的前提下，把批量保留改为
同时检查不可变的原始自动近邻风险和按当前 active 峰顶派生的实时近邻风险；任一项成立就跳过。
Windows `dist` 人工验收已经通过；`0.4.1` 从同一提交构建 Windows 和 macOS 包，并发布在同一个公开 prerelease。
