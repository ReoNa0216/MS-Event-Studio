# MS Event Studio v0.6.0 Release Notes

## 中文

本版完成 MS 事件审阅到 HRGC feature 矩阵的工作流，与 LMA Studio v0.7.0 配合使用。

- **Feature 提取**：从已保留事件生成事件 × feature 强度矩阵，保留原始强度与 NaN。结果随项目保存，可重开和导出。
- 自动定位原始 MS 文件并显示完整路径；文件移动后可重新定位。支持取消提取，结果界面只显示当前结果。
- **导出结果**按用途分为分析 ZIP 和 LMA 事件包 ZIP，均支持自定义文件名且不覆盖同名文件。分析包包含事件 CSV 和可选 H5AD；LMA 包保留全部审阅状态并可附矩阵。项目分享位于“新建 / 打开”。
- “包含待定事件”只影响分析 CSV，不扩大矩阵范围。矩阵仅含已保留事件；QC 排除只依据用户填写的时间范围，不从 MS 自动推断 QC 身份。
- 精简界面说明，显示最近项目路径，改善提取和导出的活动反馈。

**下载**：Windows x64 完整解压 `MS-Event-Studio-0.6.0-windows-x64.zip`，运行 `MS-Event-Studio.exe`，保留全部随附文件。Apple Silicon Mac 解压 `MS-Event-Studio-0.6.0-macos-arm64.zip` 后打开应用。两个 ZIP 均附 SHA-256 文件；macOS 使用 ad-hoc 签名，未进行 Developer ID 签名或公证。

**兼容**：现有 `ms-event-project-v2` 项目保留事件与审阅记录；更早的旧格式仍需从原始输入重建。原始数据只读。计算依赖固定为 flame-feature-core 0.1.0 和 flame-ms-core 0.1.1。

**验证**：Windows 工程桌面验收完成。MPP、LSK、CAR-T-Bez 真实副本完成提取、传递、UMAP 和重开验证；工程选取不作为人工真值。发布构建要求双平台各通过 189 项 Python 测试及打包后的 HRGC/WebView 检查；另有 108 项标准浏览器场景和独立审查。

macOS 无可用真机，可见界面和鼠标交互尚未验收。此前 Windows 曾有一次 CLR `0x80131506` 退出，后续桌面复测未复现，根因未确认。工程检查不等同科学标签准确率或正式投稿项目验收。

## English

This release connects reviewed MS events to HRGC feature extraction and works with LMA Studio v0.7.0.

- **Feature extraction** generates an event-by-feature matrix from retained events, preserving raw intensities and NaNs. Results are saved with the project for reopening and export.
- Automatically locate the original MS source and display its full path. Relocate moved files, cancel extraction and view the current result.
- **Export results** offers an analysis ZIP or an LMA event-package ZIP, with custom filenames and no overwriting. Analysis packages contain event CSV and optional H5AD; LMA packages preserve all review statuses and may include the matrix. Project sharing is available under New / Open.
- Including pending events affects the analysis CSV only. Matrix rows come from retained events, with exclusions based solely on user-specified time ranges. MS signals do not automatically establish QC identity.
- Simplify interface wording, display recent-project paths and improve extraction/export activity feedback.

**Installation:** Extract the complete Windows x64 ZIP and run `MS-Event-Studio.exe` with all bundled files present. On Apple Silicon macOS, extract the macOS ZIP and open the application. Both archives include SHA-256 files. The macOS app is ad-hoc signed, not Developer ID signed or notarized.

**Compatibility:** Existing `ms-event-project-v2` projects retain events and reviews; older unsupported formats still require rebuilding from the original inputs. Raw data remain read-only. Scientific dependencies are pinned to flame-feature-core 0.1.0 and flame-ms-core 0.1.1.

**Validation:** Windows engineering desktop acceptance is complete. Real MPP, LSK and CAR-T-Bez copies exercised extraction, transfer, UMAP and reopening; engineering selections are not human ground truth. Release builds require all 189 Python tests and packaged HRGC/WebView checks on each platform. Additional evidence includes 108 browser scenarios and independent reviews.

No physical Mac was available, so visible macOS interaction remains untested. One earlier Windows CLR `0x80131506` exit was not reproduced in subsequent desktop checks; its cause remains undetermined. These checks do not establish biological label accuracy or acceptance of unavailable publication projects.
