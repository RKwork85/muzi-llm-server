# Cursor 启动指令 — Skill 3

请先完整读取当前 Skill 目录中的 `SKILL.md`、`references/DEEPSEEK_API_CONTRACT.md` 和 `checklists/PHASE_ACCEPTANCE.md`。

项目根目录必须是：

`C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\qs_llm_dp`

本次任务是**按照 Skill 直接分阶段实施 M3**，不是重新设计架构或只输出方案。

先执行 Skill 的开始前检查：确认真实项目路径、Git 状态、分支、提交历史；阅读 M0/M1/M2 文档、`docs/phase2/REVIEW_REPORT.md`，以及现有 Provider、Settings、ToolRegistry、ToolExecutor、API、SSE 和测试实现。

随后严格按 `SKILL.md` 的 M3.0 → M3.6 顺序实施。每阶段先写测试、实现最小改动、运行专项测试和全量回归、更新 `docs/phase2/m3/IMPLEMENTATION_LOG.md`。不能跳阶段、不能一次性堆代码、不能用 mock 测试冒充真实 DeepSeek 联调。

本项目当前已知的两项 P2 必须在 M3.0 修复：非法 JSON 成功截断、深层/超长 JSON 参数。同步工具线程超时/取消后仍会继续运行，必须在 Runtime 中落实有界并发和执行槽生命周期管理。

严格遵守官方 DeepSeek Chat Completions / Tool Calls / Thinking Mode 协议。特别注意：若 thinking mode 启用，tool loop 后续请求必须正确回传 reasoning_content；不得无声丢弃。若当前 SDK 无法可靠支持，先阻断并说明，不得猜测字段。

如果遇到 P0/P1、安全边界失效、需要破坏第一阶段兼容性、需要新增重大依赖或必须由用户决策的接口变更，立即停止并报告，不得自行绕过。

除上述阻断情况外，按 Skill 完成全部 M3 阶段，不要在阶段间无故等待。不要执行破坏性 Git 操作；未经明确授权不创建提交、不推送、不修改 Tag。最终按 Skill 的完成标准给出真实文件清单、API、测试统计、live test 状态和剩余风险。
