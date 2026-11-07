# Cursor Continue Prompt

继续执行 Phase 2 的下一个未完成里程碑。

在当前项目根目录继续。相对路径以该根目录为准。不要回到已废弃的 `qs-test/qs_llm_dp` 目录，也不要混合读取旧目录中的代码。

开始前重新读取：
- `SKILL.md`
- `references/01_PHASE2_ARCHITECTURE.md`
- `references/02_RUNTIME_CONTRACTS.md`
- `references/03_SECURITY_AND_FAILURES.md`
- `references/04_TEST_AND_ACCEPTANCE.md`

遵守以下要求：
1. 先检查上一个里程碑的代码和测试状态。
2. 只实现一个里程碑，不提前扩展后续范围。
3. 保持 Phase 1 v0.1.0 契约不变。
4. 新增代码必须有针对性测试。
5. 先跑聚焦测试，再跑完整测试。
6. 报告修改文件、接口变化、测试结果、风险和下一步。
7. 若发现设计冲突或需要新增依赖，先说明并等待确认，不擅自决定。
