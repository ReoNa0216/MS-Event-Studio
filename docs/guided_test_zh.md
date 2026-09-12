# Windows 联合验收：MS Feature → LMA 原生 UMAP

使用本机 `dist` 测试版，先用 `studio-validation/MPP/` 中的完整项目副本，约 5–10 分钟。不要在原始研究项目上试写操作；Release 待人工确认后发布。

1. 启动 MS 的 `dist/windows/MS-Event-Studio/MS-Event-Studio.exe`，打开父工作区 `studio-validation/MPP/MS`。
2. 打开“Feature 提取”，确认有 1,023 个已保留事件及可用原始文件。若本机尚未记住旧项目文件位置，只需定位一次 `HSC1_data/Lin-_MPP.txt`。保持默认设置，点击“开始提取 / 重新提取”；预计得到 1,023 × 3,549 的矩阵。
3. 进入“导出结果 → 导出分析结果”，勾选包含矩阵，保存 ZIP。此次无需纯事件包入口，也无需另导坐标 CSV。
4. 启动 LMA 的 `dist/LMAStudio/LMAStudio.exe`，打开 `studio-validation/MPP/LMA`。在“配置 → 矩阵与原生 UMAP”导入刚才的 ZIP，确认事件和 feature 数相同。
5. 点击“计算 / 重新计算 UMAP”。完成后关闭配置、点顶部“UMAP”；切换“采集时间 / 人工标注”着色。应有 1,023 个点，人工标注均为未标注。
6. 关闭并重开 LMA 项目，确认 UMAP 无需重算，图和标签保持不变。

重点反馈：哪一步报错、等待是否可接受、是否有不清楚或重复的入口。直接回复“通过”，或“第几步 + 提示/截图”即可。

LSK-engineering 和 CAR-T-Bez-engineering 用于额外规模检查：使用真实原始数据，测试副本中的批量保留有工程测试审计，不是人工审阅或科学真值。它们的细胞/feature 轴各自独立，不能把不同项目矩阵直接按列拼接。本轮不评价 UMAP 的生物学分群准确率。
