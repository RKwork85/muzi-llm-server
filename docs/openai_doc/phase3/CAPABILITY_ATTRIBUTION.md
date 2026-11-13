# 能力归属实测

日期：2026-11-06。命令在设置 `DEEPSEEK_LIVE=1` 后运行 `tests/test_live_capability.py`。密钥只进入进程环境，输出已去掉密钥。结果 `1 passed in 28.54s`。

同一请求是“画一个用户登录流程图”。对照组 `toolsets=["basic"]`，实验组 `toolsets=["basic","diagram"]`。思考模式关闭。每组 3 次，全部到达 `completed`。

第一次尝试因轮询没有等待而在 0.66 秒时中断，没有形成终态记录，不计入下面 6 次。

| 组 | 次序 | 状态 | 工具调用 | 事件中的工具 | artifact 类型 | artifact 来源 | 回答里出现 Mermaid 文本 |
|---|---|---|---|---|---|---|---|
| 对照 | 1 | completed | 0 | 无 | 无 | 无 | 是 |
| 对照 | 2 | completed | 0 | 无 | 无 | 无 | 是 |
| 对照 | 3 | completed | 0 | 无 | 无 | 无 | 是 |
| 实验 | 1 | completed | 1 | `create_flowchart` | `mermaid` | `create_flowchart` | 否 |
| 实验 | 2 | completed | 2 | `create_flowchart`、`validate_mermaid` | `mermaid`、`mermaid` | 两个工具各一份 | 是 |
| 实验 | 3 | completed | 2 | `create_flowchart`、`validate_mermaid` | `mermaid`、`mermaid` | 两个工具各一份 | 是 |

对照组 3 次都由模型直接写出 Mermaid 文本，没有工具调用，也没有 artifact。实验组 3 次都出现 `create_flowchart`，并且响应里有 `type=mermaid` 的 artifact。后两次模型又调用了 `validate_mermaid`，因此多了一份 artifact，最终回答里也复述了图。实验组第一次只调用生成工具，回答没有再贴源码。

这只能说明在这 6 次、当前模型和关闭思考的条件下，把 `diagram` 放进 toolset 后服务端拿到了结构化图。它不是对所有提示词和所有模型的保证。
