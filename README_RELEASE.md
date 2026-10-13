# MS Event Studio v0.6.2 Release Notes

## 中文

本版将计算依赖升级至正式 **flame-feature-core 0.2.1**，优化单 run HRGC 的查询、工作表内存与完整溯源写出，沿用现有提取、保存和导出流程。

- 从当前已保留事件及 current apex 提取，保持 `B_owned_mz_center_v1`、局部 `B_owned_v1`、固定参数、事件与 feature ID、原始 float64 强度、NaN 和物理扫描身份。
- 已保存的 0.1.0/0.2.0 结果按原版本重开、校验和导出。主动重新提取使用 0.2.1，旧结果保留。
- 构建固定正式 wheel、SHA256、标签和源码 commit。没有 wheel secret 时，从正式 Release 下载并核对 wheel、SHA256SUMS.txt 和 release-manifest.json。打包冒烟记录实际 core 位置和运行源码哈希。
- `flame-ms-core` 保持 0.1.1。与 LMA Studio v0.7.2 配合，矩阵按稳定事件 ID/版本导入和附加标签；沿用单 run 产品流程。

**验证**：MS 192 项、仓库外安装 core 27 项、LMA 26 项相关回归，以及 108 页截图矩阵和三项独立审查通过。真实 MPP 隔离副本的 1,023 × 3,549 结果与 core 0.2.0 的矩阵、NaN、轴及全部溯源逐值一致；保存重开、两类 ZIP、LMA 矩阵更新和带标签导出保真。Windows 同环境后续运行各一次：core API 51.094→36.875 s，完整提取保存 67.422→52.984 s，子进程峰值工作集 3.13→1.42 GiB；首次编译另行记录。打包程序的 WebView/计算子进程检查及实际页面提取通过。原生点击和文件选择器的完整手动链路仍需复核，本轮原生输入自动化未完成。

**安装**：完整解压 `MS-Event-Studio-0.6.2-windows-x64.zip`，运行 `MS-Event-Studio.exe`。Apple Silicon Mac 使用 `MS-Event-Studio-0.6.2-macos-arm64.zip`。两个 ZIP 均附 SHA256。Mac 为 ad-hoc 签名，未公证；macOS 真机可见交互和未取得的正式投稿项目尚未逐项验收。

## English

This release upgrades computation to the official **flame-feature-core 0.2.1**, reducing redundant queries, worksheet memory and complete-provenance writing costs in the existing single-run workflow.

- Extraction uses current accepted events and their reviewed apexes. Method `B_owned_mz_center_v1`, local method `B_owned_v1`, fixed parameters, event/feature IDs, original float64 intensities, NaNs and physical scan identities are preserved.
- Saved 0.1.0/0.2.0 results reopen, verify and export with their original version records. Explicit re-extraction uses 0.2.1 and retains previous results.
- Builds pin the official wheel, SHA256, tag and source commit. Without a wheel secret, the resolver downloads and verifies the official wheel, SHA256SUMS.txt and release-manifest.json. Packaged smoke reports include the actual core location and runtime source hashes.
- `flame-ms-core` remains at 0.1.1. LMA Studio v0.7.2 imports matrices and joins labels by stable event ID/version. The product continues to process one run per project.

**Validation:** 192 Studio tests, 27 out-of-source installed-core tests, 26 related LMA tests, 108 screenshot scenarios and three independent reviews passed. The isolated real MPP result (1,023 × 3,549) exactly matches core 0.2.0 in values, NaNs, axes and every provenance field. Reopen, both ZIP formats, LMA matrix updates and labeled exports preserve data. One warm measurement per version on the same Windows environment reduced core API time from 51.094 to 36.875 s, complete extraction/save from 67.422 to 52.984 s, and worker peak working set from 3.13 to 1.42 GiB; cold compilation is recorded separately. Packaged WebView/worker checks and extraction through the actual application page passed. Complete native click/file-picker operation awaits manual verification because native input automation could not complete this round.

**Installation:** Extract the complete Windows x64 archive and launch `MS-Event-Studio.exe`. Use the macOS ARM64 archive for Apple Silicon. Both include SHA256 files. The Mac app is ad-hoc signed and not notarized. Visible interaction on a physical Mac and project-by-project acceptance of unavailable publication projects remain outstanding.
