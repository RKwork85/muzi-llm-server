# 给 Cursor / 开发 Agent 的执行规范

## 第 0 步：只读审计
先审计 `muzi-llm-server` 的 chat/agents/planning API、SSE、Tool Registry、数据库迁移、Ask 运行记录、Plan 交互与事件表，以及 Vue 3 的聊天渲染、状态管理、路由和主题系统。输出“已存在 / 部分存在 / 缺失 / 冲突 / 不确定”矩阵；不得假设目录结构与本规划一致。

## 每阶段执行循环
1. 输出现状证据和变更影响清单。
2. 写 ADR、API/Schema、数据库迁移和前端映射。
3. 先写失败用例，后做最小纵向切片。
4. 补安全/并发/异常/旧版兼容测试。
5. 演示并记录证据，得到授权再进入下一阶段。

## 推荐分支与提交
每阶段独立 feature branch；数据库只做加法迁移；采用 feature flag 开关；协议版本固定；禁止大规模无关重构。

## 给开发 Agent 的提示词模板
```text
你是 muzi-llm-server / Vue3 的架构实施工程师。
本轮仅执行 Mx，依据压缩包中的 Mx 文件、ARCHITECTURE 和 ACCEPTANCE_MATRIX。
先只读审计真实仓库，输出证据和差异，再提交实施方案。
未经确认不得跨阶段、重写现有 Ask/Plan/Agent Runtime、改变权限语义或删除旧协议。
所有模型输出视为不可信，后端负责授权与状态机。
交付代码、测试、迁移、兼容方案、回滚方案、验收证据。
无法验证的项目标记 BLOCKED，不得声称完成。
```
