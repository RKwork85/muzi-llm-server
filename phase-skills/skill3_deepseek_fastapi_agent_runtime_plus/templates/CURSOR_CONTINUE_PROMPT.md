# Cursor 中断后续做指令 — Phase 3 Tool Ecosystem

继续执行 Phase 3 工具生态任务。

项目根目录：C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\qs_llm_dp
Skill 目录：C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\qs_llm_dp\phase-skills\skill3_deepseek_fastapi_agent_runtime_plus

先重新读取 SKILL.md、references/04_DEEPSEEK_SEARCH_FETCH_CONTRACT.md 和 docs/phase3/IMPLEMENTATION_LOG.md，检查 Git 状态和当前代码。不要假设上次执行成功，不要覆盖我的修改。

找出最后一个通过专项测试和全量回归的里程碑，从其后第一个未完成里程碑继续，顺序 T0 → T1 → T2 → T3 → T6。当前里程碑若有失败测试，先修复，不得跳过。

沿用启动指令中【我已做出的决定】的全部内容。规则不变：默认行为不变、toolset 由服务端决定、联网工具 async、URL 预校验与来源约束、路径沙箱、结果合法 JSON、Deadline/取消/并发上限、日志脱敏。

不重做已验收阶段，不虚报测试，不执行破坏性 Git 操作，未经授权不提交不推送不改 Tag。T4/T5 只出设计文档。遇到 P0/P1 或需要我决定的事，立即停止说明。
