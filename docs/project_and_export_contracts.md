# Project and export contracts

Task 1 candidate exports machine contract v2 with explicit scientific settings.
The shared core validates and publishes it; LMA consumes the complete package
without re-calling MS events. See [Task 1 integration](flame_task1.md) and the core
`docs/event-package-v2.md`. Existing project and review DB schemas are unchanged.

## Project v2

`ms_event_project.json` is the root manifest. All runtime paths are portable
project-relative paths and are rejected if they are absolute, drive-relative,
ADS-like, contain `..`, or resolve through a symlink outside the root.

Required immutable artifacts are the project README, scan summary Parquet,
automatic event Parquet, input manifest, detector protocol, and processing log.
Their role and path are unique; size and complete SHA-256 are checked before a
project opens. `annotations/review.sqlite` is mutable but is bound internally and
in the manifest to the same project ID, generation ID, and schema version.

Creation builds a sibling directory named
`.TARGET.ms-event-building-<uuid>`. Nothing is published until parsing,
detection, all writes, and a full open-project preflight succeed. Cancellation
or failure cleans only that validated staging directory and preserves an
existing empty target.

The original multi-GB source is externally referenced and read only. Its
absolute path is deliberately not serialized; the input manifest stores file
name, complete SHA-256, size, mtime, and head/tail hashes. Range recalculation
still asks for a source. Feature extraction reuses a local source hint when
available and verifies the complete fingerprint during reading; otherwise it
asks the user to locate the file.

The manifest and detector protocol bind one `scientific_settings` object:
the project primary marker m/z, fixed closed ±12 ppm extraction window, fixed
782.5616 quality-control marker, and the nearby-event review threshold. The
primary marker is also bound to source inspection and parser output. A marker
change therefore creates a new v2 project; v1 projects are not reinterpreted or
migrated.

This binding guarantees reproducibility, not scientific equivalence between
markers. The default 760.5851 path has real-source regression evidence; an
alternative marker currently has only synthetic extraction/serialization tests
until a marker-specific real dataset is adjudicated. The nearby-event threshold
is stored with the project but remains review-risk policy: it does not enter the
automatic-event identity or suppress detection.

## Range generation changes

An analysis-range change is preview-only until explicit confirmation. The
preview binds the root-manifest hash, complete review-state token, detector
payload hash, proposed reconciliation, and new generation identity. Apply fails
if any bound state changed.

Confirmed apply holds a SQLite writer reservation while it creates a unique
generation activation. The retired review database is copied to a new immutable
archive before the root manifest switches; this prevents a stale second
application from corrupting bound history if it later writes the obsolete path.
The new automatic table, detector protocol, active review database, and retired
review archive are complete before the single atomic manifest replacement.
Failed post-switch validation restores the old manifest and removes only the
recognized orphan activation.

Exact or confirmed unique mappings retain project EventID and status. Ambiguous
or unmatched old automatic reviews remain with `generation_state=stale`;
in-range manual events remain active/manual, while out-of-range manual events
become stale. Recalculation clears the old generation's undo stack but preserves
all audit rows and appends `recalculate_analysis_range` with the confirmed diff.

## Human CSV v1

The exact columns are:

```text
EventID,scan_id,scan_start_time,apex_intensity,review_status,source
```

Time is decimal minutes derived from integer nanoseconds. Default status is
`accepted`; `pending` requires an explicit switch. Range ownership is closed and
uses the current apex. Rejected and unreviewed rows never enter this CSV.

## Machine contract v2 (LMA 事件包)

An atomic machine-export directory contains:

- `manifest.json`: source fingerprint, detector/parameter/generation binding,
  closed range rule, status counts, event-table schema, size, and SHA-256;
- `events.parquet`: all current statuses plus stable EventID, immutable
  automatic identity/evidence, original support, current apex, review revision,
  and provenance;
- `checksums.sha256`: SHA-256 for both files above.

The table contains all review statuses from the active generation. Stale
generation history is preserved in the project but excluded from both export
contracts. The desktop export chooser selects an existing parent directory and
the application publishes a new, uniquely named child directory atomically;
the user never has to prepare an empty target. Consumers must filter review
status explicitly. This contract is not permission to overwrite an LMA Studio
`ms_events.parquet`.

## Project sharing ZIP

See [project sharing](project_sharing.md). This copies a complete project for reopening in the same Studio; it is separate from the event exchange package. Sharing is read-only and does not append an export audit row.


## HRGC feature 结果（开发分支）

一个项目对应一个原始 MS run。Feature 提取重读外部原文件，验证全量 SHA256、文件大小、物理扫描行、scan ID、spectrum index 和时间；以当前审阅峰顶及前后各一扫描调用独立 `flame-feature-core 0.1.0`。强度阈值 200、支持比例 0.2、10 ppm、m/z 100–1050 固定。只纳入 accepted，显式 QC 时间段按闭区间峰顶时间排除；细胞标签及 MS barcode 不参与提取。

每次成功结果保存为 `features/<opaque result id>/`。保留包原生 `native_matrix.h5ad`（float64、NaN）、feature 轴、事件行、质量信息与代表点；另附原始 v2 `source_events/` 和含所有事件排除原因的 `event_inclusion.parquet`。`execution_record.json` 记录原始来源、逐事件版本、依赖、固定参数、QC 段、适配器哈希与所有产物哈希。项目 manifest 和 SQLite 审阅不改动，不把预测写成 accepted。

计算在可终止子进程中进行，临时输出位于项目外；保存前复核审阅绑定，经同卷原子发布进入项目。失败/取消无部分结果，旧结果保留。事件改变后旧结果显示过期；损坏历史结果单独报不可用，不阻断新提取。导出严格复核哈希并拒绝覆盖目标，项目 ZIP 自动包含完整 feature 结果。

浏览器沿用原生文件/目录 capability：`GET /api/features` 返回计数、结果与绑定；`POST /api/features/extract` 接收 source_token、binding、qc_intervals（字符串分钟对）；`POST /api/features/export` 接收 result_id、target_token。两种 POST 返回既有 job，提取可取消；同会话提取与审阅/范围修改互斥。用户要求的最近项目与原始 MS 文件以 display_path 展示本机完整路径；提交仍只接受原生选择器登记的 token，不接受浏览器提交路径。

创建项目或成功提取后，在本机 recent-project 配置旁的 `.sources.json` 文件记录完整 raw SHA256 → 位置。它只用于定位，不进入项目/分享包；写入失败不改变计算结果。打开 Feature 时仅检查存在性与大小，返回文件名、本机展示路径和 opaque token，不把 located 当作内容已经校验。实际提取仍完整核验 SHA256 与物理扫描。旧项目没有位置记录时首次定位一次；错误/取消提取不写入新提示。缺失 raw 不影响已有矩阵导出。

「导出结果」按用途提供分析结果和 LMA 交接，两者都可附带最近一次且仍有效的矩阵。一个分析 ZIP 包含 `events.csv`、`analysis_record.json`，以及选定当前结果的完整 `features/`（含 v2 `source_events/`）。`POST /api/exports/analysis` 使用 binding、可选 result_id、target_token、include_pending 与 note，并接受可选 filename；任务与范围修改互斥，导出前后核验版本，成功仅记一条审计。原始文件缺失不阻止导出，过期矩阵不能与新事件 CSV 混合。Feature 提取窗口只负责计算及结果概览。两种用途均可自命名 ZIP，默认“项目名-分析结果.zip”或“项目名-LMA事件包.zip”；校验 Windows 文件名，缺少扩展名时补齐，遇到同名文件拒绝覆盖。导出页「传给 LMA Studio」通过 `POST /api/exports/lma` 生成 LMA 事件包 ZIP：`events/` 保留正式 v2 三文件，`handoff_record.json` 绑定事件 manifest 哈希和可选矩阵记录哈希，`features/` 仅在选择矩阵时加入。LMA 校验交接用途、事件与矩阵一致性；分析 ZIP 不作为 LMA 导入入口。原 v2 文件夹导入仍可用于已发布格式；完整项目分享入口位于「新建 / 打开 → 分享当前项目…」。

本轮不合并多个项目的特征轴。单 run 验证通过不等于五套研究矩阵复现；多 run 必须一次组轴，不能直接按列拼接各项目矩阵。LMA 联合验收候选已按同一事件 ID 与版本接入矩阵，并独立保存预处理/UMAP 参数和坐标；UMAP 不属于 HRGC 原始矩阵。

## LMA 原生 UMAP

Windows 联合候选在新建项目中接收 LMA 事件包 ZIP，含矩阵时一并导入，无矩阵也能建项对齐；已有项目的配置入口仅补充或更新同事件、同版本的矩阵。旧保存项目不迁移；用户参考代码、实现差异、旧项目兼容边界及验证记录统一维护在相邻 LMA 仓库的 [接入说明](../../lma-studio/docs/flame_task1.md)。MS 不增加 Scanpy 依赖。共享交接按用户要求待 Windows UAT、双平台 Release 后再同步。
