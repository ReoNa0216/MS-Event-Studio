# MS Event Studio v0.6.1 Release Notes

## 中文

本版将 feature 计算依赖升级至正式发布的 **flame-feature-core 0.2.0**，与 LMA Studio v0.7.2 配合使用。

- 从已保留事件提取、随项目保存、重开、分析 ZIP 和 LMA 事件包导出的操作保持不变。
- 已保存的 0.1.0 矩阵仍可直接重开和导出，不自动重算。主动重新提取时使用 0.2.0，保留旧结果及其版本记录。
- core 0.2.0 使用 `mz_center` 做多 run 组轴，方法记录为 `B_owned_mz_center_v1`；Studio 仍按单项目、单 run 提取，未新增跨项目合并功能。单 run 的强度、NaN 和局部 feature 身份不变。
- 构建固定官方 wheel、SHA-256 和源码 commit；`flame-ms-core` 继续使用 0.1.1，事件检峰及人工审阅语义不变。

**Windows 验证**：189 项 Studio 测试、19 项 core 测试和打包后的 WebView/HRGC 子进程检查通过；108 个标准浏览器页面及独立审查完成。真实 MPP 为 1,023 × 3,549，升级前后的数值、NaN、事件表、特征轴及局部代表完全一致；真实 LSK 为 1,794 × 2,888。MPP 经 LMA Studio v0.7.2 完成旧/新矩阵导入、保存重开及按事件 ID 附加标签导出，原始矩阵值保持不变。工程副本中的保留和配对不构成科学真值。

**安装**：完整解压 `MS-Event-Studio-0.6.1-windows-x64.zip`，运行 `MS-Event-Studio.exe`，不要只复制 EXE。Apple Silicon Mac 使用 `MS-Event-Studio-0.6.1-macos-arm64.zip`。两个 ZIP 均附 SHA-256；Mac 为 ad-hoc 签名，未公证。发布构建要求双平台各通过 189 项测试与打包 HRGC/WebView 检查；没有可用 Mac，真机界面验收仍待完成。

## English

This release upgrades feature computation to the officially released **flame-feature-core 0.2.0** and works with LMA Studio v0.7.2.

- Extraction from retained events, project persistence, reopening, analysis ZIP export and LMA handoff remain unchanged.
- Saved 0.1.0 matrices remain readable and exportable without recomputation. Explicit re-extraction uses 0.2.0 while preserving previous results and their provenance.
- Core 0.2.0 groups multiple runs by `mz_center`, recorded as `B_owned_mz_center_v1`. Studio still extracts one run per project and does not expose cross-project merging. Single-run intensities, NaNs and local feature identities are unchanged.
- Builds pin the official wheel, SHA-256 and source commit. `flame-ms-core` stays at 0.1.1; event detection and human review semantics are unchanged.

**Windows validation:** 189 Studio tests, 19 core tests and packaged WebView/HRGC subprocess smoke checks passed; 108 standard browser pages and independent reviews were completed. Real MPP data produced 1,023 × 3,549 with exact equality of values, NaNs, event records, feature axes and local representatives before/after the upgrade. Real LSK data produced 1,794 × 2,888. LMA Studio v0.7.2 imported old/new MPP matrices, reopened the saved project and exported labels joined by stable event ID without changing matrix values. Engineering selections and test relations are not scientific ground truth.

**Installation:** Extract the complete Windows x64 ZIP and launch `MS-Event-Studio.exe`. Use the macOS ARM64 ZIP for Apple Silicon. Both archives include SHA-256 files. The Mac app is ad-hoc signed and not notarized. Release builds require 189 tests and packaged HRGC/WebView checks on each platform. Physical Mac interface acceptance remains outstanding.
