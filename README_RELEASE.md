# MS Event Studio v0.5.0 Release Notes

MS Event Studio v0.5.0 正式版，提供 Windows x64 和 macOS Apple Silicon 桌面包。

## 本版更新

- 解析与 MS calling 使用固定版本的共用 `flame-ms-core 0.1.0` 内核，保留 MS-only 科学边界及现有审阅语义。
- 原“完整审计数据包”更名为“LMA 事件包”，采用正式 v2 格式，可导入 LMA Studio v0.6.0，保留事件身份、位置、版本和审阅/纳入状态。
- 新增“打包分享项目”：生成可解压继续审阅的项目 ZIP，保留数据库与操作历史，自动排除 macOS 系统元数据。
- 三种导出分别为审阅结果 CSV、LMA 事件包和项目 ZIP。可自行选择保存位置；事件包和 ZIP 只需选择已有父目录，不必预建空文件夹。
- 加固本地服务拒绝未授权请求时的响应处理，并保留原有 Origin 和会话令牌校验。

## 下载使用

- Windows：完整解压 `MS-Event-Studio-0.5.0-windows-x64.zip`，运行 `MS-Event-Studio.exe`，保留完整文件夹。
- macOS：解压 `MS-Event-Studio-0.5.0-macos-arm64.zip`，打开随附 `.app`，适用于 Apple Silicon。
- 两个 ZIP 均附 `.sha256` 校验文件。macOS 包为 ad-hoc 签名，未进行 Apple Developer ID 签名/公证；首次启动可能需在系统设置中允许打开。

## 兼容与验证

继续使用 v0.4.0 引入的 `ms-event-project-v2` 项目格式；既有 v2 项目保留事件与审阅记录。更早的旧格式项目仍需从只读原始 MS 文件重新创建，不进行静默迁移。

发布工作流从同一标签构建两平台，通过完整自动测试、截图矩阵结构检查及打包隐藏启动/科学冒烟后发布。开发阶段已完成 108 张标准浏览器截图矩阵、三个独立工程/UI/QA 审查和真实项目 ZIP 解压重开逐表/逐文件核对。

本版由维护者明确授权正式发布；课题组实际标注 UAT 仍待完成，v0.4.1 的 Windows 人工验收仅作为历史证据。macOS 隐藏启动不替代真机可见交互验收。

原始 MS 数据只读，应用与源码不含用户数据。项目 ZIP 不自动收集项目外引用的 raw。详见 [Task 1 接入](docs/flame_task1.md) 与 [项目分享](docs/project_sharing.md)。
