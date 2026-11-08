# 决策

## D1 使用工作区里的后端目录

实施说明中的路径没有对应目录。代码和文档放在 `muzi-llm/muzi-llm-server`。

## D2 不覆盖旧的 docs/phase3

该目录记录的是另一轮能力扩展。Skill System 文档只用 `docs/phase3-skill-system/`。

## D3 组织作用域和项目作用域先拒绝写入

库里只有 `users`，没有组织或项目成员。领域枚举保留 `system`、`organization`、`user`、`project`。M2 只允许注册 `system` 和 `user`。`organization` 与 `project` 的安装请求返回未启用，避免做出没有成员校验的共享 Skill。

## D4 同名 Skill 记录冲突

同一作用域内同名不覆盖。不同作用域可以同名，发现时按 system、user 的可见性分开返回，并带上各自标识，不合并成一个。

## D5 第三方脚本默认关闭

`run_python` 没有 CPU、内存和网络隔离。Skill 包中的 scripts 在具备独立沙箱之前不执行。

## D7 激活不写入安装状态

安装状态停在 enabled 或 disabled。一次任务的版本记在 SkillActivation 上。禁用不取消已有激活，只拒绝新的激活。卸载仍要等激活计数归零。

## D8 远程来源先拒绝

`SkillSourceKind.REMOTE` 留在枚举里。`assert_source_enabled()` 拒绝它，避免出现没有拉取实现的远程注册。

## D9 注册索引先落在文件

M2 把元数据写成注册表目录里的 JSON，正文仍是 `SKILL.md`。数据库表留到 M6。清单里省略版本号时按 `1.0.0` 登记。

## D6 现有四条提示词保持原行为

在 M4 把对话接到新加载器之前，不删除 `app/prompts/skills.py`，避免 Plan/Agent 提示回退。
