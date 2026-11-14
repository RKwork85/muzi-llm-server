# Muzi LLM · Generative UI 七阶段实施规划

版本：v1.0（规划文档，不代表代码已实施）

## 目标
为现有 muzi-llm-server（FastAPI）与 Vue 3 前端建设模型驱动 UI：混合消息、组件注册、模型选择、流式恢复、交互闭环、局部更新及三模式集成。

## 阅读顺序
M1 → M2 → M3 → M4 → M5 → M6 → M7。每个阶段独立文件包含目标、任务拆解、交付物、验收标准、风险。另见 `ARCHITECTURE.md`、`ACCEPTANCE_MATRIX.md`、`IMPLEMENTATION_GUIDE.md`。

## 项目边界
这是可交付给开发 Agent/Cursor 的任务规划，不是已经完成的代码审计。建议先审计真实代码、已有 M4 Plan 表及 Ask 运行持久化，再执行。

## 完成门禁
任何阶段都不得只凭“代码写完”验收；至少需要契约测试、权限与并发测试、真实 UI 演示和回滚演练。
