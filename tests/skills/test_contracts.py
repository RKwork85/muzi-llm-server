"""Skill 领域契约：字段、状态机、路径和协议形状。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.skills.errors import SkillScopeError, SkillStateError, SkillValidationError
from app.skills.models import (
    DiscoveryRequest,
    Skill,
    SkillAction,
    SkillActivation,
    SkillActor,
    SkillContext,
    SkillInstallation,
    SkillManifest,
    SkillMetadata,
    SkillPermission,
    SkillScope,
    SkillSource,
    SkillSourceKind,
    SkillStatus,
)
from app.skills.paths import normalize_resource_path
from app.skills.protocols import (
    SkillDiscovery,
    SkillLifecycleManager,
    SkillLoader,
    SkillRegistry,
    SkillResourceManager,
    SkillRuntime,
    SkillSecurity,
    SkillValidator,
)
from app.skills.state import (
    assert_scope_writable,
    assert_source_enabled,
    can_activate,
    close_activation,
    next_activation,
    transition,
)

_SHA = "a" * 64


def _meta(**overrides: object) -> SkillMetadata:
    body: dict[str, object] = {
        "skill_id": "sk_weekly01",
        "name": "weekly-report",
        "description": "整理本周完成、进行中和阻塞。",
        "version": "1.0.0",
        "scope": SkillScope.USER,
        "status": SkillStatus.ENABLED,
        "source": SkillSourceKind.LOCAL_DIRECTORY,
        "owner_id": "user-1",
    }
    body.update(overrides)
    return SkillMetadata.model_validate(body)


def _skill(status: SkillStatus = SkillStatus.ENABLED, active_count: int = 0) -> Skill:
    return Skill(
        metadata=_meta(status=status),
        source=SkillSource(kind=SkillSourceKind.LOCAL_DIRECTORY, label="local"),
        instruction_sha256=_SHA,
        active_count=active_count,
    )


def test_metadata_rejects_bad_name_and_hides_body() -> None:
    with pytest.raises(ValidationError):
        _meta(name="Weekly Report")
    meta = _meta()
    assert "instruction" not in meta.model_dump()


def test_manifest_requires_digest_and_limits_metadata() -> None:
    manifest = SkillManifest(
        name="weekly-report",
        description="整理周报",
        version="1.2.0",
        metadata={"owner": "muzi"},
        instruction_sha256=_SHA,
        body_bytes=120,
    )
    assert manifest.body_bytes == 120
    with pytest.raises(ValidationError):
        SkillManifest(
            name="weekly-report",
            description="整理周报",
            version="1.2",
            instruction_sha256=_SHA,
            body_bytes=120,
        )


def test_permission_declaration_is_not_a_grant() -> None:
    permission = SkillPermission(
        skill_id="sk_weekly01",
        scope=SkillScope.USER,
        owner_id="user-1",
        declared_tools=("search_text", "read_text_file"),
    )
    assert "handler" not in permission.model_fields
    with pytest.raises(ValidationError):
        SkillPermission(
            skill_id="sk_weekly01",
            scope=SkillScope.USER,
            declared_tools=("search_text", "search_text"),
        )


def test_organization_and_remote_are_rejected() -> None:
    with pytest.raises(SkillScopeError) as scope_error:
        assert_scope_writable(SkillScope.ORGANIZATION)
    assert scope_error.value.code == "SKILL_SCOPE_UNAVAILABLE"
    with pytest.raises(SkillStateError) as source_error:
        assert_source_enabled(SkillSourceKind.REMOTE)
    assert source_error.value.code == "SKILL_SOURCE_UNAVAILABLE"
    assert_scope_writable(SkillScope.USER)
    assert_source_enabled(SkillSourceKind.ZIP)


def test_installation_follows_the_state_table() -> None:
    status = transition(None, SkillAction.IMPORT, active_count=0)
    status = transition(status, SkillAction.REGISTER, active_count=0)
    status = transition(status, SkillAction.INSTALL, active_count=0)
    status = transition(status, SkillAction.ENABLE, active_count=0)
    assert status == SkillStatus.ENABLED
    assert transition(status, SkillAction.DISABLE, active_count=1) == SkillStatus.DISABLED
    with pytest.raises(SkillStateError) as error:
        transition(SkillStatus.ENABLED, SkillAction.UNINSTALL, active_count=1)
    assert error.value.code == "SKILL_IN_USE"
    assert transition(SkillStatus.DISABLED, SkillAction.UNINSTALL, active_count=0) == SkillStatus.UNINSTALLED
    with pytest.raises(SkillStateError):
        transition(SkillStatus.UNINSTALLED, SkillAction.ENABLE, active_count=0)


def test_activation_pins_version_and_does_not_change_installation() -> None:
    skill = _skill()
    can_activate(skill)
    assert next_activation(None).value == "active"
    with pytest.raises(SkillStateError):
        can_activate(_skill(SkillStatus.DISABLED))
    activation = SkillActivation(
        activation_id="ac_weekly01",
        skill_id="sk_weekly01",
        version="1.0.0",
        user_id="user-1",
        task_id="task-1",
        status=next_activation(None),
        instruction_sha256=_SHA,
    )
    updated = _skill()
    assert updated.metadata.version == "1.0.0"
    assert activation.version == "1.0.0"
    assert close_activation(activation.status).value == "deactivated"


def test_resource_path_stays_inside_the_package() -> None:
    path, kind = normalize_resource_path("references/guide.md")
    assert path == "references/guide.md"
    assert kind == "reference"
    for raw in ("../secrets.txt", "/etc/passwd", "scripts/../../x", "SKILL.md", "notes/a.md"):
        with pytest.raises(SkillValidationError):
            normalize_resource_path(raw)


def test_context_marks_instructions_untrusted() -> None:
    context = SkillContext(user_id="user-1", mode="ask", budget_chars=2000)
    assert context.untrusted is True
    request = DiscoveryRequest(
        actor=SkillActor(user_id="user-1"),
        mode="agent",
        intent="写周报",
        explicit_names=("weekly-report",),
    )
    assert request.explicit_names == ("weekly-report",)
    installation = SkillInstallation(
        installation_id="in_weekly01",
        skill_id="sk_weekly01",
        version="1.0.0",
        scope=SkillScope.USER,
        owner_id="user-1",
        status=SkillStatus.ENABLED,
    )
    assert installation.status == SkillStatus.ENABLED


def test_protocols_declare_the_required_methods() -> None:
    expected = {
        SkillRegistry: {"register", "get", "query", "update", "uninstall"},
        SkillDiscovery: {"discover"},
        SkillLoader: {"load_instructions", "load_resource"},
        SkillRuntime: {"activate", "deactivate"},
        SkillValidator: {"validate"},
        SkillSecurity: {"visible", "declared_tools"},
        SkillResourceManager: {"list_resources"},
        SkillLifecycleManager: {"apply", "record"},
    }
    for protocol, names in expected.items():
        assert names <= set(protocol.__protocol_attrs__)
