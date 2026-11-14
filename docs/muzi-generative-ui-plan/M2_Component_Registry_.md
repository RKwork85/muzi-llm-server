# M2｜Component Registry 与 Vue 动态渲染

## 阶段目标
建立可发现、可校验、可版本化的组件能力目录和安全渲染层。

## 前置条件
前序阶段验收通过；确认既有实现与本阶段契约兼容。

## 详细工作包
### M2-T01 组件分类
- 工作内容：Primitive、Composite、Semantic；初期注册 text、choice.single、choice.multiple、metric、chart.bar、data.table、plan.review。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M2-T02 注册表契约
- 工作内容：组件 key、semver、props_schema、事件 schema、allowed_modes、风险级别、客户端兼容版本、fallback。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M2-T03 服务端发现
- 工作内容：按模式、用户权限、客户端能力、租户策略筛选组件；返回精简 manifest，禁止暴露内部敏感组件。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M2-T04 前端映射
- 工作内容：componentRegistry.ts + defineAsyncComponent；BlockRenderer、UnknownBlock、ErrorBoundary。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M2-T05 渲染安全
- 工作内容：禁止模型注入 HTML/JS/Vue 模板；属性白名单、URL 校验、Markdown 消毒、CSP 和资源限制。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M2-T06 状态隔离
- 工作内容：Block 稳定 key、局部状态与服务端状态分层、避免组件重排丢失输入。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M2-T07 性能与无障碍
- 工作内容：懒加载、骨架屏、键盘可达、焦点管理、响应式布局、深浅主题适配。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

## 关键验收标准
- [ ] 注册组件按 manifest 可发现并正确渲染
- [ ] 未知组件不崩溃且显示 fallback
- [ ] 不允许模型输出事件处理器代码或任意 URL
- [ ] 键盘操作、移动端和主题切换可用

## 建议文件位置（仅建议，审计后调整）
- `app/ui/registry.py`
- `src/generative-ui/registry/componentRegistry.ts`
- `src/generative-ui/renderer/BlockRenderer.vue`
- `tests/ui/test_registry.py`
- `tests/e2e/generative-ui.spec.ts`

## 主要风险与防护
- 协议漂移：服务端 schema、客户端类型、测试样例统一生成或双向契约测试。
- 权限绕过：所有状态变更都在服务端复核，UI 组件不是授权凭证。
- 兼容性：加法迁移、feature flag、旧版 fallback、回滚路径。
- 竞态与重放：稳定 ID、事务、版本号、幂等键和事件序列。

## 统一工程约束
- 以现有 FastAPI / Vue 3 / PostgreSQL / Redis / SSE 为基线，先审计再修改；不得以规划推断当前实现状态。
- 后端是身份、授权、交互状态和事件序列的唯一可信来源；模型输出一律视为不可信输入。
- 协议必须版本化，旧客户端能安全降级；所有写操作具备鉴权、资源归属、幂等和审计。
- 每个阶段均需单元、集成、端到端、安全、兼容性测试；没有证据不得标记 ACCEPTED。
- 不擅自修改 Ask/Plan/Agent 现有语义，不以新 UI 协议替代 Runtime 权限边界。

## 阶段交付要求
- 提交架构决策记录（ADR）、协议/数据库迁移说明、测试报告、演示录屏或截图、回滚方案。
- 明确 CODE PASS、UAT PASS、PRODUCTION READY 三种不同状态。
- 每项验收需列出可复现命令、测试用例编号、预期结果、实际证据和已知限制。
