"""Skill 领域模型。这里只描述数据和约束，不访问数据库，也不执行工具。"""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_TOOL = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SkillScope(str, Enum):
    SYSTEM = "system"
    ORGANIZATION = "organization"
    USER = "user"
    PROJECT = "project"


WRITABLE_SCOPES = frozenset({SkillScope.SYSTEM, SkillScope.USER})


class SkillStatus(str, Enum):
    """安装记录的状态。一次任务里的激活不改这个值。"""

    IMPORTED = "imported"
    REGISTERED = "registered"
    INSTALLED = "installed"
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNINSTALLED = "uninstalled"


class ActivationStatus(str, Enum):
    ACTIVE = "active"
    DEACTIVATED = "deactivated"
    FAILED = "failed"


class SkillSourceKind(str, Enum):
    BUILTIN = "builtin"
    LOCAL_DIRECTORY = "local_directory"
    ZIP = "zip"
    REMOTE = "remote"


ENABLED_SOURCES = frozenset(
    {
        SkillSourceKind.BUILTIN,
        SkillSourceKind.LOCAL_DIRECTORY,
        SkillSourceKind.ZIP,
    }
)


class SkillAction(str, Enum):
    IMPORT = "import"
    REGISTER = "register"
    INSTALL = "install"
    ENABLE = "enable"
    DISABLE = "disable"
    UPDATE = "update"
    ROLLBACK = "rollback"
    UNINSTALL = "uninstall"


class ResourceKind(str, Enum):
    REFERENCE = "reference"
    ASSET = "asset"
    SCRIPT = "script"


class ExecutionStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class SkillActor(BaseModel):
    """调用方身份。组织标识和项目标识可以出现，但当前不能据此授权。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str | None = Field(default=None, max_length=64)
    organization_id: str | None = Field(default=None, max_length=64)
    project_id: str | None = Field(default=None, max_length=64)

    @field_validator("user_id", "organization_id", "project_id")
    @classmethod
    def _check_id(cls, value: str | None) -> str | None:
        if value is not None and _ID.fullmatch(value) is None:
            raise ValueError("标识不合法")
        return value


class SkillSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SkillSourceKind
    label: str = Field(min_length=1, max_length=120)


class SkillMetadata(BaseModel):
    """发现阶段可见的字段。不包含 SKILL.md 正文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(pattern=r"^sk_[a-z0-9]{8,32}$")
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    version: str
    scope: SkillScope
    status: SkillStatus
    source: SkillSourceKind
    owner_id: str | None = Field(default=None, max_length=64)
    license: str | None = Field(default=None, max_length=120)
    compatibility: str | None = Field(default=None, max_length=240)
    category: str | None = Field(default=None, max_length=32)
    tags: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if _NAME.fullmatch(value) is None:
            raise ValueError("技能名称不合法")
        return value

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("版本号不合法")
        return value

    @field_validator("owner_id")
    @classmethod
    def _check_owner(cls, value: str | None) -> str | None:
        if value is not None and _ID.fullmatch(value) is None:
            raise ValueError("所有者标识不合法")
        return value

    @field_validator("category")
    @classmethod
    def _check_category(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[a-z0-9-]{1,32}", value) is None:
            raise ValueError("分类不合法")
        return value

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 8 or len(value) != len(set(value)):
            raise ValueError("标签不合法")
        for tag in value:
            if re.fullmatch(r"[a-z0-9-]{1,32}", tag) is None:
                raise ValueError("标签不合法")
        return value


class SkillManifest(BaseModel):
    """清单。instruction_sha256 指向文件，正文不放进这个对象。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    version: str
    license: str | None = Field(default=None, max_length=120)
    compatibility: str | None = Field(default=None, max_length=240)
    metadata: dict[str, str] = Field(default_factory=dict)
    instruction_sha256: str
    body_bytes: int = Field(ge=1, le=65536)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if _NAME.fullmatch(value) is None:
            raise ValueError("技能名称不合法")
        return value

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("版本号不合法")
        return value

    @field_validator("instruction_sha256")
    @classmethod
    def _check_sha(cls, value: str) -> str:
        if _SHA.fullmatch(value) is None:
            raise ValueError("内容摘要不合法")
        return value

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 16:
            raise ValueError("元数据过多")
        for key, item in value.items():
            if not isinstance(key, str) or not 1 <= len(key) <= 32:
                raise ValueError("元数据键不合法")
            if not isinstance(item, str) or len(item) > 120:
                raise ValueError("元数据值不合法")
        return value


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metadata: SkillMetadata
    source: SkillSource
    instruction_sha256: str
    active_count: int = Field(default=0, ge=0)

    @field_validator("instruction_sha256")
    @classmethod
    def _check_sha(cls, value: str) -> str:
        if _SHA.fullmatch(value) is None:
            raise ValueError("内容摘要不合法")
        return value


class SkillVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(pattern=r"^sk_[a-z0-9]{8,32}$")
    version: str
    instruction_sha256: str
    sequence: int = Field(ge=1)

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("版本号不合法")
        return value

    @field_validator("instruction_sha256")
    @classmethod
    def _check_sha(cls, value: str) -> str:
        if _SHA.fullmatch(value) is None:
            raise ValueError("内容摘要不合法")
        return value


class SkillInstallation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    installation_id: str = Field(pattern=r"^in_[a-z0-9]{8,32}$")
    skill_id: str = Field(pattern=r"^sk_[a-z0-9]{8,32}$")
    version: str
    scope: SkillScope
    owner_id: str | None = Field(default=None, max_length=64)
    status: SkillStatus

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("版本号不合法")
        return value


class SkillActivation(BaseModel):
    """一次任务固定的技能版本。升级安装记录不会改这里。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    activation_id: str = Field(pattern=r"^ac_[a-z0-9]{8,32}$")
    skill_id: str = Field(pattern=r"^sk_[a-z0-9]{8,32}$")
    version: str
    user_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    conversation_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    status: ActivationStatus
    instruction_sha256: str

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("版本号不合法")
        return value

    @field_validator("instruction_sha256")
    @classmethod
    def _check_sha(cls, value: str) -> str:
        if _SHA.fullmatch(value) is None:
            raise ValueError("内容摘要不合法")
        return value


class SkillResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(pattern=r"^sk_[a-z0-9]{8,32}$")
    version: str
    relative_path: str = Field(min_length=1, max_length=180)
    kind: ResourceKind
    size_bytes: int = Field(ge=0, le=262144)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _check_sha(cls, value: str) -> str:
        if _SHA.fullmatch(value) is None:
            raise ValueError("内容摘要不合法")
        return value


class SkillPermission(BaseModel):
    """技能声明的工具需求。声明本身不授予执行权。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(pattern=r"^sk_[a-z0-9]{8,32}$")
    scope: SkillScope
    owner_id: str | None = Field(default=None, max_length=64)
    declared_tools: tuple[str, ...] = ()

    @field_validator("declared_tools")
    @classmethod
    def _check_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 16 or len(value) != len(set(value)):
            raise ValueError("声明的工具不合法")
        if any(_TOOL.fullmatch(name) is None for name in value):
            raise ValueError("声明的工具不合法")
        return value


class SkillExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str = Field(pattern=r"^ex_[a-z0-9]{8,32}$")
    skill_id: str = Field(pattern=r"^sk_[a-z0-9]{8,32}$")
    version: str
    user_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    conversation_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    status: ExecutionStatus
    error_code: str | None = Field(default=None, max_length=64)


class SkillContext(BaseModel):
    """交给现有上下文管理器的激活摘要。正文另按预算加载。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    conversation_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    mode: Literal["ask", "plan", "agent"]
    activations: tuple[SkillActivation, ...] = ()
    budget_chars: int = Field(ge=0, le=8000)
    untrusted: Literal[True] = True


class SkillQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actor: SkillActor
    scope: SkillScope | None = None
    status: SkillStatus | None = None
    name: str | None = Field(default=None, max_length=64)


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actor: SkillActor
    mode: Literal["ask", "plan", "agent"]
    intent: str = Field(min_length=1, max_length=4000)
    explicit_names: tuple[str, ...] = ()

    @field_validator("explicit_names")
    @classmethod
    def _check_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 8 or len(value) != len(set(value)):
            raise ValueError("指定的技能不合法")
        if any(_NAME.fullmatch(name) is None for name in value):
            raise ValueError("指定的技能不合法")
        return value


class LoadedInstructions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(pattern=r"^sk_[a-z0-9]{8,32}$")
    version: str
    instruction_sha256: str
    text: str = Field(min_length=1, max_length=8000)
    untrusted: Literal[True] = True


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    errors: tuple[str, ...] = ()
