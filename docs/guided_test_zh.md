# MS Event Studio 中文引导测试

这套测试使用程序自动生成的 2 分钟、1,201 个扫描的临时数据。它不会读取或修改
LMA Studio 项目，也不应把测试项目建在真实数据目录中。

## 第一轮：5 分钟冒烟测试

1. 保持整个 `MS-Event-Studio` 文件夹不变，运行其中的
   `MS-Event-Studio.exe`。不要只复制单独的 EXE。
2. 在欢迎页点击 **Start guided test**，选择一个专门放临时测试文件的目录。
   程序会创建唯一命名的 Source TXT，并预填一个尚不存在的 Project 目录。
3. 在 New project 窗口点击 **Analyze source**。预期结果是 1,201 scans，闭区间
   0–2 min；随后点击 **Create project**。
4. 主界面应显示三个自动顶点：0.5、1.0、1.5 min。0.75 min 附近的小峰故意低于
   自动阈值，因此此时不应有事件标记。
5. 任意选择一个自动事件，确认右侧出现真实 scan、PC34、MS782、TIC、m/z/ppm、
   prominence、width 与 quality flags。再依次试用 Accepted、Rejected、Pending、
   Unreviewed；界面不应卡死，状态标记的形状和文字也应变化。

这五步通过后，安装包、解析、检测、绘图、选择与审阅写入的主链路已经可用。

## 第二轮：完整鼠标 UAT

1. 在 **Reason** 输入框键入 `a r p u`，确认只是正常输入；焦点回到绘图区后，
   A/R/P/U 快捷键才应改变状态。
2. 点击 **Add event [+]**，再点 0.75 min 附近的小峰。预期新增一个绑定真实扫描、
   默认 Accepted 的人工事件，并显示 snap offset；在已有自动事件 support 内点击时，
   应定位已有事件而不是制造重复项。
3. 选择自动事件，点击 **Adjust apex [M]**，在其 immutable support 内选择附近扫描；
   support 外的点击必须失败且不能悄悄改动事件。
4. 测试 **Undo [Ctrl+Z]** 与 **Redo [Ctrl+Y]**，关闭并重新打开项目，确认状态、
   人工事件与撤销历史仍存在。
5. 打开 **Export** 页签：导出 human CSV，分别检查 pending 关闭/开启；再导出 machine
   contract 到一个新的空目录，确认有 manifest、Parquet 和 SHA-256 sidecar。
6. 点击 **Change range...**，输入 0.6–2 min。先阅读 diff，再二次确认应用。0.5-min
   旧事件应进入 stale/history；新导出不得混入 stale 历史。
7. 再次关闭并打开项目，确认 active range、review、manual event 与 audit-backed
   Undo/Redo 状态均保留。

遇到问题时记录：执行到哪一步、弹窗原文、期望与实际结果，以及一张完整窗口截图。
测试数据和项目用完后可手动删除；程序不会自动删除它们。
