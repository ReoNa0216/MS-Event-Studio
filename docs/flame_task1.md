# FLAME 任务 1 接入说明

任务 1 正式版使用 `flame-ms-core==0.1.0`。当前 feature 开发分支改用 `0.1.1`，只扩展 PyArrow 24 依赖兼容范围，解析、calling 和 v2 事件合同不变。两 Studio 不互相导入产品代码。

## 安装与构建

当前固定源码见 `packaging/computation.json`：MS core `0899d60`（0.1.1）、feature core `7eb67c0`（0.1.0）。Python 3.11 环境运行 `python scripts/resolve_computation.py`，再安装 `pip install -e .`。解析脚本从相邻 Git 仓库的精确 commit 导出干净源码；没有本地仓库时通过已认证的 GitHub CLI clone。不会安装相邻仓库的未提交改动，也不会创建发布。

Windows/Mac 构建脚本使用同一来源锁。CI 需要可读取两个私有仓库的 `FLAME_COMPUTATION_READ_TOKEN`（也接受具备相同权限的既有 `FLAME_MS_CORE_READ_TOKEN`）；旧 base64 wheel 流程已删除。平台验收状态见共享交接。

## 事件传递

项目 ZIP 与事件包用途不同，见[项目分享](project_sharing.md)。

MS Event Studio 导出“LMA 事件包”，LMA 新建项目选择“LMA 事件包”，提供原 MS 文件和 LIF 输入。CSV 审阅结果供人阅读；正式传递必须完整包，不能手改列名替代。

v2 包包含 `events.parquet`、`manifest.json`、`checksums.sha256`。完整合同见内核 `docs/event-package-v2.md`。原始自动身份包含 raw SHA、方法版本和 generation；当前事件身份、修订、原始及当前 scan/时间/支持窗、审阅状态分别保留。两个 Studio 的项目 UUID 可不同。

LMA 导入保留所有事件及顺序，只有 accepted 进入标注名单。pending、rejected、unreviewed 仍留在工作表和不可变原包中。raw 严格解析仅用于曲线及原始/当前物理位置校验，导入不重新 MS calling，不走旧 CSV roster 补峰。

LMA 项目配置可只读检查新版审阅包，逐事件比较新增/删除/修改及生成版本。更新创建新项目；不在原项目原位替换事件或自动迁移标注。同名 ID 的峰顶、窗口、状态变化也被识别。发布前完整校验在 sibling staging 完成，失败回滚，原项目不受影响。

## 旧项目与科学边界

v0.4.0+ 项目加载沿用已保存事件、配对、标签、模型、名单顺序与绑定。无编辑打开不初始化/迁移 DB，不重算、不刷新绑定。缺 DB 或绑定错误明确失败。SQLite 关闭最后一个读取连接可能移除原本为空的 WAL 及配套 SHM；持久化科学内容不变。测试始终使用隔离副本。

新建独立分析使用共用 caller，得到稳定自动来源身份。旧 LMA 用扫描间隔近似计算峰宽，新核用实际采集时间；新分析不能覆盖历史投稿结果。±15 ppm 人工名单支持通道保留，自动 primary 使用 ±12 ppm，二者不合并。

任务 2 等待专门数据，仅未来验收 label-correct；人工标签不是独立真值。标签与 feature 按事件 ID 并行产出。LIF→MS 采集时间对齐 QC 与跨批参照细胞不同，FLAME 没有色谱保留时间。HSC 特定参数不作为通用默认。测量、背景/质量证据、置信度、算法表示、化学注释和 metabolic state 分别表达。任务 3 由 Windows 先接入 MS，LMA 矩阵及原生 UMAP 延后；任务 4 只保留接口，未实现自动标签或批次校正模型。

## 验收证据

当前构建、真实数据与旧项目的最终结果见共享仓库 `handoff/WINDOWS_STATUS.md` 及任务 1 验收报告。本机原项目不等于尚未取得的正式投稿项目，不能宣称逐投稿项目验收。Windows 人工 UAT 后再安排 macOS 真机可见验收。
