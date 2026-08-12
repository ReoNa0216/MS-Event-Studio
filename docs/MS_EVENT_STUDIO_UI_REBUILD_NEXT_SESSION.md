# 新 Session 开头语

复制下面整段作为新 Session 的第一条消息：

```text
请完整读取以下两个文件，然后严格从 UX-R0 开始执行，不要重新讨论已确认的架构决定：

E:\ChenZhi\Tsinghua\Scientific_research\Hu Lab\scMetab\ms-event-studio\AGENTS.md
E:\ChenZhi\Tsinghua\Scientific_research\Hu Lab\scMetab\ms-event-studio\docs\MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md

先检查 git status、当前 HEAD 和 handoff 记录的 a587b37 科学代码基线，运行现有完整测试。
随后调用三个独立 subagent：分别负责 LMA WebView/视觉复用审查、科学服务/API 边界审查、
交互/可访问性/打包 QA 审查；LMA Studio v0.4.4 只读，绝对不能修改，也不能成为运行时依赖。

不要继续给现有 ttk 审阅页做字体、圆角或 padding 补丁。按 handoff 迁移到与 LMA Studio
一致的 pywebview + HTML/CSS/SVG 壳，保留已经通过测试的科学、项目、审阅、范围、审计
与导出核心。先完成 UX-R0 护栏与 UX-R1 WebView 应用壳；每一步都按 handoff 的退出条件
测试、截图并让 subagent 审查。不要把未经过标准截图矩阵和 agent pre-UAT 的页面交给我
找问题。Windows 迁移后必须带并测试 MS-Event-Studio.exe.config；macOS ARM64 继续通过
GitHub Actions 的 macos-14 原生构建。过程中用中文给我简洁进度，发现科学契约冲突时立即
停止扩大改动并明确报告。
```
