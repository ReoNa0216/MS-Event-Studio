# GitHub Actions 桌面构建与公开测试

MS Event Studio 只使用一个公开仓库：
[`ReoNa0216/MS-Event-Studio`](https://github.com/ReoNa0216/MS-Event-Studio)。源码、问题反馈、
Windows x64 与 macOS Apple Silicon 下载都在同一仓库；不再维护单独的 Releases 仓库。

## 日常候选构建

在 GitHub 打开 **Actions → Build and release desktop packages → Run workflow**：

1. `platform` 选 `all`，确保 Windows 与 macOS 来自同一个提交；
2. `version` 填应用版本，例如 `0.4.0`，不要加 `.dev1`；
3. 首次审计保持 `publish_prerelease` 关闭，只下载 Actions artifacts 检查；
4. 两个平台构建和隐藏启动均通过后，再以相同提交运行并打开
   `publish_prerelease`，供真实用户下载测试。

手动候选的 GitHub 标签为 `candidate-<version>`，但软件内部版本和 ZIP 文件名仍是干净的
`<version>`。正式稳定版使用 `v<version>` Git 标签；工作流会移除标签前缀 `v` 后再生成
ZIP，因此文件名不会出现多余的 `v`。

## 每个平台实际证明什么

- Windows 由 `windows-2022` 构建 x64 onedir 包，强制 Edge Chromium/WebView2，拒绝
  Tk/Tcl、Android、旧 MSHTML 和非 x64 Loader。
- macOS 由 `macos-14` 的 ARM64 Python 构建 `.app`，强制 Cocoa，执行隐藏 WebView/API/
  科学冒烟，ad-hoc 签名后再次验证并刷新最终清单。
- 两边都先运行完整 Python 测试与截图矩阵结构检查，再生成 ZIP 和 SHA-256 sidecar。
- Actions 的 macOS 隐藏启动不能替代 Apple Silicon 真机上的 Retina 可见界面和鼠标体验；
  因此首次跨平台发布应标为 prerelease，收到真机反馈后再转为正式版。

## 本地成品目录

`dist/` 只保留当前平台的最终候选：Windows 为 `dist/windows`，macOS runner 为
`dist/macos`。中间构建、截图和诊断证据放在 ignored 的 `build/`，发布 ZIP 放在
`release/`。不要再创建 `dist/windows-ux-*` 之类的临时目录。

## 版本 0.4.0

`0.4.0` 是新的 `ms-event-project-v2` 测试版。它增加项目级主 marker、相邻事件提示阈值、
当前窗口安全事件批量保留、跨平台导出修复和启动优化。旧测试项目必须从只读 MS 原始文件
重新创建，不做可能错误解释科学列的兼容迁移。
