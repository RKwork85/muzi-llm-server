"""Skill 领域协议。实现放在后续里程碑，这里只固定输入和输出。"""

from __future__ import annotations

from typing import Protocol

from app.skills.models import (
    DiscoveryRequest,
    LoadedInstructions,
    Skill,
    SkillAction,
    SkillActivation,
    SkillActor,
    SkillContext,
    SkillExecution,
    SkillInstallation,
    SkillManifest,
    SkillMetadata,
    SkillPermission,
    SkillQuery,
    SkillResource,
    SkillScope,
    SkillSource,
    SkillVersion,
    ValidationReport,
)


class SkillRegistry(Protocol):
    def register(
        self,
        manifest: SkillManifest,
        instructions: str,
        *,
        actor: SkillActor,
        source: SkillSource,
        scope: SkillScope,
    ) -> Skill:
        """登记一份清单和对应正文。同作用域同名时拒绝，不覆盖已有记录。"""

    def get(self, skill_id: str, *, actor: SkillActor) -> Skill:
        """按标识读取调用方可见的技能。"""

    def query(self, query: SkillQuery) -> list[SkillMetadata]:
        """返回元数据。不返回正文。"""

    def update(
        self,
        skill_id: str,
        manifest: SkillManifest,
        instructions: str,
        *,
        actor: SkillActor,
    ) -> SkillVersion:
        """追加一个版本。正在使用的激活仍指向旧版本。"""

    def uninstall(self, skill_id: str, *, actor: SkillActor) -> Skill:
        """没有进行中的激活时标记卸载。"""


class SkillDiscovery(Protocol):
    def discover(self, request: DiscoveryRequest) -> list[SkillMetadata]:
        """按身份、状态和意图返回候选元数据。"""


class SkillLoader(Protocol):
    def load_instructions(self, skill_id: str, version: str, *, actor: SkillActor) -> LoadedInstructions:
        """按固定版本读取 SKILL.md 正文。正文视为不可信。"""

    def load_resource(
        self,
        skill_id: str,
        version: str,
        relative_path: str,
        *,
        actor: SkillActor,
    ) -> SkillResource:
        """按相对路径读取引用、素材或脚本声明。脚本默认不执行。"""


class SkillRuntime(Protocol):
    def activate(self, skill_id: str, *, actor: SkillActor, mode: str, task_id: str | None) -> SkillContext:
        """启用中的技能才能激活。上下文只带预算内的不可信说明。"""

    def deactivate(self, activation_id: str, *, actor: SkillActor) -> SkillActivation:
        """结束一次激活。不改变安装版本。"""


class SkillValidator(Protocol):
    def validate(self, manifest: SkillManifest) -> ValidationReport:
        """检查名称、版本和清单字段。"""


class SkillSecurity(Protocol):
    def visible(self, skill: Skill, actor: SkillActor) -> bool:
        """用户只能看到系统和自己的技能。"""

    def declared_tools(self, permission: SkillPermission) -> tuple[str, ...]:
        """返回声明的工具名。声明不等于执行许可。"""


class SkillResourceManager(Protocol):
    def list_resources(self, skill_id: str, version: str, *, actor: SkillActor) -> list[SkillResource]:
        """列出某一固定版本的资源索引，不返回文件正文。"""


class SkillLifecycleManager(Protocol):
    def apply(
        self,
        installation: SkillInstallation,
        action: SkillAction,
        *,
        actor: SkillActor,
        active_count: int,
    ) -> SkillInstallation:
        """按状态机推进安装记录。"""

    def record(self, execution: SkillExecution) -> SkillExecution:
        """保存一次与用户、会话和任务关联的执行结果。"""
