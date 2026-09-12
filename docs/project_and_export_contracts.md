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

浏览器沿用原生文件/目录 capability：`GET /api/features` 返回计数、结果与绑定；`POST /api/features/extract` 接收 source_token、binding、qc_intervals（字符串分钟对）；`POST /api/features/export` 接收 result_id、target_token。两种 POST 返回既有 job，提取可取消；同会话提取与审阅/范围修改互斥。接口不暴露本机绝对路径。

创建项目或成功提取后，在本机 recent-project 配置旁的 `.sources.json` 文件记录完整 raw SHA256 → 位置。它只用于定位，不进入项目/分享包；写入失败不改变计算结果。打开 Feature 时仅检查存在性与大小，返回文件名和 opaque token，不把 located 当作内容已经校验。实际提取仍完整核验 SHA256 与物理扫描。旧项目没有位置记录时首次定位一次；错误/取消提取不写入新提示。缺失 raw 不影响已有矩阵导出。

矩阵唯一入口为「导出结果 → 导出分析结果」中的可选复选框。一个 `analysis-*.zip` 包含 `events.csv`、`analysis_record.json`，以及选定当前结果的完整 `features/`（含 v2 `source_events/`）。`POST /api/exports/analysis` 使用 binding、可选 result_id、target_token、include_pending 与 note；任务与范围修改互斥，导出前后核验版本，成功仅记一条审计。原始文件缺失不阻止导出，过期矩阵不能与新事件 CSV 混合。Feature 提取窗口只负责计算及结果概览。导出页第二个用途保留 v2「传给 LMA Studio」事件包；完整项目分享入口位于「新建 / 打开 → 分享当前项目…」。

本轮不合并多个项目的特征轴。单 run 验证通过不等于五套研究矩阵复现；多 run 必须一次组轴，不能直接按列拼接各项目矩阵。LMA 后续以同一事件 ID 与版本接入矩阵，再保存预处理/UMAP 参数和坐标；UMAP 不属于 HRGC 原始矩阵。

## 用户提供的 UMAP 参考与旧项目边界（待 LMA 实现）

2026-09-12 用户提供 Scanpy `embed_and_cluster` 参考；这里只记录研究入口，不增加 Scanpy 运行依赖，也不宣称已复现原图。参考函数复制 AnnData；支持 PCA、t-SNE、UMAP、Leiden，默认 `n_pcs=50`、`n_neighbors=15`、`leiden_resolution=1.0`、`random_state=42`，可按 obs 字段着色。PCA 分支显式 `sc.tl.pca(..., svd_solver='arpack')`；各分支之后均调用 `sc.pp.neighbors(..., n_neighbors=n_neighbors, n_pcs=n_pcs)`，UMAP 分支调用 `sc.tl.umap(..., random_state=random_state)`。用户实际使用的调用为：

```python
madata_hrgc = mc.pp.fill_nan_values(madata_hrgc, fill_method='zero')
madata_hrgc = embed_and_cluster(
    madata_hrgc, method='umap', random_state=1, color='scan_start_time'
)
madata_hrgc.obs['UMAP1'] = madata_hrgc.obsm['X_umap'][:, 0]
madata_hrgc.obs['UMAP2'] = madata_hrgc.obsm['X_umap'][:, 1]
```

产品接入时需明确：

- `mc.pp.fill_nan_values` 的具体实现和原环境版本未提供，不能声称上述行为已逐值复现。零填充是用户提供的降维参考方案，只在独立计算副本使用；HRGC 原始 float64/NaN 矩阵保留。
- 原函数只有 PCA 分支显式计算 PCA；UMAP 分支会依赖 `neighbors` 的自动表示选择及可能已有的 PCA。应显式记录所用表示、实际 PCA 维数和邻居数，处理小样本维数限制，避免沿用来源不明的旧 PCA。固定随机性时同时记录 PCA、邻居图与 UMAP 的随机设置；示例仅给 UMAP/t-SNE 传入函数种子。
- 不因参考函数包含 t-SNE/Leiden 就扩大首轮范围。首先完成矩阵 → 二维 UMAP；不默认增加归一化、log、缩放、删 feature 或批次校正。采集时间只用于着色，不作为距离计算的输入列。记录输入矩阵/事件版本、填零策略、计算参数、依赖版本及输出坐标。
- 当前 LMA 已能读取成对的 UMAP1/UMAP2，并允许没有 UMAP 的事件项目。原生计算应增加可选坐标来源，沿用显示层；新版打开旧项目时保留原坐标、事件、人工标签、配对和时间模型，不要求矩阵、不自动计算或迁移。新坐标作为独立结果保存，显式切换才用于显示。
- 原生矩阵按共同来源、事件 ID 和版本附加。旧项目中仅有时间匹配坐标或不同调用事件身份时，不能仅凭时间近似给矩阵行强行绑定；需要明确验证或使用独立新项目。兼容目标是新版读取已有 v0.4.0+ 项目，不能提前承诺旧版可读取新增结果格式。
- 实现阶段仍须用旧项目副本验证打开/保存/重开后的事件、标签、配对、时间模型及既有坐标保真；当前仅核对读取逻辑，没有执行 LMA 兼容性验收。共享交接按用户要求待 Windows UAT、双平台 Release 后再同步。

实现参考：[Scanpy neighbors](https://scanpy.readthedocs.io/en/stable/api/generated/scanpy.pp.neighbors.html)、[Scanpy UMAP](https://scanpy.readthedocs.io/en/stable/generated/scanpy.tl.umap.html)。API 的稳定版与开发版随机数参数不同，接入时固定实际依赖版本，不照搬隐式默认值。
