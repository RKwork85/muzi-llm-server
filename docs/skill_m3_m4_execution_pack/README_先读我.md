# Skill System · M3 + M4 执行包

版本：1.0  
适用项目：`muzi-llm/muzi-llm-server`  
目标：在现有 M0–M2 基础上完成 Skill Discovery + Skill Runtime，并接入现有 Chat / Agent 执行链路。

## 使用方式

1. 将本压缩包解压到工作区之外的临时目录，或放到项目 `docs/phase3-skill-system/` 下的独立子目录；不要覆盖现有文档。
2. 在 Cursor 中打开实际后端目录 `muzi-llm/muzi-llm-server`。
3. 将 `09_CURSOR_MASTER_EXECUTION_PROMPT.md` 的完整内容交给 Cursor Agent。
4. 让 Cursor 先完成只读审查和方案校准，用户确认后再编码。
5. 按 `10_EXECUTION_CHECKLIST.md` 分批实施；M3 与 M4 可以在同一交付周期完成，但必须分别验收。
6. 最后按 `08_ACCEPTANCE_MATRIX.md` 验收，并生成真实测试报告。

## 项目基线（不可擅自改变）

- 当前工作区存在 MCP、插件、代码执行等未提交改动。
- `muzi-llm` 和 `muzi-llm-server` 目前均不是 Git 仓库；不得假设存在可用 Git 提交检查点。
- 实际后端目录为 `muzi-llm/muzi-llm-server`，不要使用错误路径 `muzi_project/muzi-llm-server`。
- M0–M2 已完成：Skill 领域模型、状态机、协议、文件注册表、SKILL.md 解析、目录扫描、ZIP 导入、查询、卸载和用户隔离。
- 安装状态与任务激活状态分离；禁用 Skill 不得篡改正在使用的版本快照。
- 组织/项目作用域目前因缺少成员表而拒绝写入，不得绕过。
- 第三方脚本目前只存储、不执行。M3/M4 不得引入脚本执行能力。
- 现有 `app/prompts/skills.py` 中 `research`、`polish`、`minutes`、`weekly` 四条旧提示必须保留并兼容。
- `tests/skills` 当前 15 项通过；全量测试曾有 307 通过、10 跳过、2 失败。两个失败位于 `tests/database/test_metadata.py`，其旧业务表数量断言与新增 MCP 表不一致。本阶段不得擅自修改该测试或相关 MCP 表来“清绿”。
- 已有文档基线位于 `docs/phase3-skill-system/`；不得覆盖旧 `docs/phase3/`。

## 本包目录

- `00_PROJECT_BASELINE.md`：项目事实、保护范围和不变量
- `01_PRODUCT_REQUIREMENTS.md`：M3+M4 产品目标与非目标
- `02_TARGET_ARCHITECTURE.md`：分层架构、信息流和职责边界
- `03_DISCOVERY_SPEC.md`：M3 详细规格
- `04_RUNTIME_SPEC.md`：M4 详细规格
- `05_CONTRACTS_AND_DATA_FLOW.md`：数据契约与模块交互要求
- `06_INTEGRATION_GUIDE.md`：Chat / Agent 接入要求
- `07_FAILURE_SECURITY_BOUNDARIES.md`：异常、安全边界和并发要求
- `08_ACCEPTANCE_MATRIX.md`：验收矩阵和一票否决项
- `09_CURSOR_MASTER_EXECUTION_PROMPT.md`：交给 Cursor 的总执行提示词
- `10_EXECUTION_CHECKLIST.md`：分批执行和交付清单
- `11_REFERENCE_MATERIALS.md`：官方参考资料及使用说明

## 重要说明

本包是工程需求、设计约束和验收基线，不是对仓库现状的代码审计结论。Cursor 必须先读取真实代码、测试、配置和文档，再将本包中的目标映射到实际模块。若本包与已落地代码冲突，以经核实的代码事实为准，并先提交差异分析，不得直接推翻现有实现。
