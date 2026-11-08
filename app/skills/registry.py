"""文件型技能注册表。正文留在包目录，索引里只有元数据。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import tempfile
import zipfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from app.skills.errors import SkillError, SkillScopeError, SkillStateError, SkillValidationError
from app.skills.frontmatter import parse_skill_markdown
from app.skills.models import (
    Skill,
    SkillAction,
    SkillActor,
    SkillBundle,
    SkillManifest,
    SkillMetadata,
    SkillQuery,
    SkillScope,
    SkillSource,
    SkillSourceKind,
    SkillStatus,
    SkillVersion,
)
from app.skills.paths import normalize_resource_path
from app.skills.state import assert_scope_writable, assert_source_enabled, can_activate, transition

_MAX_PACKAGE = 1_000_000
_MAX_RESOURCE = 262144


class _VersionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    instruction_sha256: str
    sequence: int


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    name: str
    description: str
    version: str
    scope: SkillScope
    status: SkillStatus
    source_kind: SkillSourceKind
    source_label: str
    owner_id: str | None = None
    license: str | None = None
    compatibility: str | None = None
    category: str | None = None
    metadata: dict[str, str] = {}
    instruction_sha256: str
    active_count: int = 0
    versions: list[_VersionEntry]
    bundle_id: str | None = None
    bundle_name: str | None = None


class FileSkillRegistry:
    def __init__(self, root: Path, *, allow_system: bool = False) -> None:
        self._root = root
        self._allow_system = allow_system
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "records").mkdir(exist_ok=True)
        (self._root / "packages").mkdir(exist_ok=True)

    def register(
        self,
        manifest: SkillManifest,
        instructions: str,
        *,
        actor: SkillActor,
        source: SkillSource,
        scope: SkillScope,
        bundle_id: str | None = None,
        bundle_name: str | None = None,
    ) -> Skill:
        self._check_body(manifest, instructions)
        assert_scope_writable(scope)
        assert_source_enabled(source.kind)
        owner = self._owner(scope, actor)
        if self._conflict(scope, owner, manifest.name) is not None:
            raise SkillError("SKILL_NAME_CONFLICT", "同名技能已存在")
        record = _Record(
            skill_id="sk_" + secrets.token_hex(6),
            name=manifest.name,
            description=manifest.description,
            version=manifest.version,
            scope=scope,
            status=SkillStatus.REGISTERED,
            source_kind=source.kind,
            source_label=source.label,
            owner_id=owner,
            license=manifest.license,
            compatibility=manifest.compatibility,
            category=_category(manifest),
            metadata=dict(manifest.metadata),
            instruction_sha256=manifest.instruction_sha256,
            versions=[
                _VersionEntry(
                    version=manifest.version,
                    instruction_sha256=manifest.instruction_sha256,
                    sequence=1,
                )
            ],
            bundle_id=bundle_id,
            bundle_name=bundle_name,
        )
        self._write_package(record, instructions)
        self._save(record)
        return self._skill(record)

    def get(self, skill_id: str, *, actor: SkillActor) -> Skill:
        return self._skill(self._visible(skill_id, actor))

    def query(self, query: SkillQuery) -> list[SkillMetadata]:
        found: list[SkillMetadata] = []
        for record in self._records():
            if not self._can_see(record, query.actor):
                continue
            if query.scope is not None and record.scope != query.scope:
                continue
            if query.status is not None and record.status != query.status:
                continue
            if query.name is not None and record.name != query.name:
                continue
            found.append(self._metadata(record))
        return sorted(found, key=lambda item: item.name)

    def update(
        self,
        skill_id: str,
        manifest: SkillManifest,
        instructions: str,
        *,
        actor: SkillActor,
    ) -> SkillVersion:
        self._check_body(manifest, instructions)
        record = self._visible(skill_id, actor)
        if manifest.name != record.name:
            raise SkillValidationError("SKILL_NAME_LOCKED", "不能在更新时改名")
        if any(item.version == manifest.version for item in record.versions):
            raise SkillError("SKILL_VERSION_EXISTS", "该版本已经存在")
        status = transition(record.status, SkillAction.UPDATE, active_count=record.active_count)
        record.metadata = dict(manifest.metadata)
        record.version = manifest.version
        record.description = manifest.description
        record.license = manifest.license
        record.compatibility = manifest.compatibility
        record.category = _category(manifest)
        record.instruction_sha256 = manifest.instruction_sha256
        record.status = status
        self._write_package(record, instructions)
        record.versions.append(
            _VersionEntry(
                version=manifest.version,
                instruction_sha256=manifest.instruction_sha256,
                sequence=len(record.versions) + 1,
            )
        )
        self._save(record)
        latest = record.versions[-1]
        return SkillVersion(
            skill_id=record.skill_id,
            version=latest.version,
            instruction_sha256=latest.instruction_sha256,
            sequence=latest.sequence,
        )

    def uninstall(self, skill_id: str, *, actor: SkillActor) -> Skill:
        record = self._visible(skill_id, actor)
        record.status = transition(record.status, SkillAction.UNINSTALL, active_count=record.active_count)
        self._save(record)
        return self._skill(record)

    def apply(self, skill_id: str, action: SkillAction, *, actor: SkillActor) -> Skill:
        record = self._visible(skill_id, actor)
        record.status = transition(record.status, action, active_count=record.active_count)
        self._save(record)
        return self._skill(record)

    def reserve(self, skill_id: str, *, actor: SkillActor) -> Skill:
        record = self._visible(skill_id, actor)
        can_activate(self._skill(record))
        record.active_count += 1
        self._save(record)
        return self._skill(record)

    def release(self, skill_id: str, *, actor: SkillActor) -> Skill:
        record = self._visible(skill_id, actor)
        if record.active_count < 1:
            raise SkillStateError("SKILL_NOT_ACTIVE", "技能不在激活中")
        record.active_count -= 1
        self._save(record)
        return self._skill(record)

    def import_zip(self, payload: bytes, *, actor: SkillActor, label: str, scope: SkillScope) -> Skill:
        archive, members = _open_zip(payload)
        try:
            text = members["SKILL.md"].decode("utf-8")
            manifest, body = parse_skill_markdown(text)
            skill = self.register(
                manifest,
                body,
                actor=actor,
                source=SkillSource(kind=SkillSourceKind.ZIP, label=label),
                scope=scope,
            )
            package = self._package(skill.metadata.skill_id, skill.metadata.version)
            for name, data in members.items():
                if name == "SKILL.md":
                    continue
                relative, _kind = normalize_resource_path(name)
                target = package / relative
                self._ensure_inside(package, target)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            return skill
        finally:
            archive.close()

    def scan_directory(
        self,
        directory: Path,
        *,
        actor: SkillActor,
        label: str,
        scope: SkillScope,
        bundle_id: str | None = None,
        bundle_name: str | None = None,
    ) -> list[Skill]:
        root = directory.resolve()
        if not root.is_dir():
            raise SkillValidationError("SKILL_SOURCE_INVALID", "技能目录无效")
        imported: list[Skill] = []
        for path in sorted(root.rglob("SKILL.md")):
            resolved = path.resolve()
            if not resolved.is_file() or os.path.commonpath([str(root), str(resolved)]) != str(root):
                continue
            manifest, body = parse_skill_markdown(resolved.read_text(encoding="utf-8"))
            skill = self.register(
                manifest,
                body,
                actor=actor,
                source=SkillSource(kind=SkillSourceKind.LOCAL_DIRECTORY, label=label),
                scope=scope,
                bundle_id=bundle_id,
                bundle_name=bundle_name,
            )
            self._copy_tree(resolved.parent, skill.metadata.skill_id, skill.metadata.version)
            imported.append(skill)
        return imported

    def import_bundle(
        self,
        payload: bytes,
        *,
        actor: SkillActor,
        name: str,
        scope: SkillScope,
    ) -> SkillBundle:
        """上传一个技能包 zip：整体安装，内含一个或多个技能。

        解压到临时目录后按目录扫描，所有技能共享同一个 bundle_id。
        校验通过后自动安装并启用，用户看到的只是一个技能包。
        """
        clean_name = _bundle_name(name)
        base = _extract_bundle(payload)
        try:
            bundle_id = "bu_" + secrets.token_hex(6)
            skills = self.scan_directory(
                base,
                actor=actor,
                label=clean_name,
                scope=scope,
                bundle_id=bundle_id,
                bundle_name=clean_name,
            )
            if not skills:
                raise SkillValidationError("SKILL_PACKAGE_INVALID", "技能包缺少说明")
            for skill in skills:
                self.apply(skill.metadata.skill_id, SkillAction.INSTALL, actor=actor)
                self.apply(skill.metadata.skill_id, SkillAction.ENABLE, actor=actor)
            return self._bundle(bundle_id, actor=actor)
        finally:
            import shutil

            shutil.rmtree(base, ignore_errors=True)

    def list_bundles(self, *, actor: SkillActor) -> list[SkillBundle]:
        groups: dict[str, SkillBundle] = {}
        for record in self._records():
            if record.status == SkillStatus.UNINSTALLED:
                continue
            if not self._can_see(record, actor):
                continue
            bundle_id = record.bundle_id
            if not bundle_id:
                continue
            bundle = groups.get(bundle_id)
            if bundle is None:
                groups[bundle_id] = SkillBundle(
                    bundle_id=bundle_id,
                    name=record.bundle_name or record.source_label,
                    scope=record.scope,
                    owner_id=record.owner_id,
                    source_label=record.source_label,
                    skill_count=1,
                    skills=(self._metadata(record),),
                )
            else:
                groups[bundle_id] = bundle.model_copy(
                    update={
                        "skill_count": bundle.skill_count + 1,
                        "skills": bundle.skills + (self._metadata(record),),
                    }
                )
        return sorted(groups.values(), key=lambda item: item.name)

    def uninstall_bundle(self, bundle_id: str, *, actor: SkillActor) -> SkillBundle:
        records = [
            record
            for record in self._records()
            if record.bundle_id == bundle_id and record.status != SkillStatus.UNINSTALLED
        ]
        if not records:
            raise SkillError("BUNDLE_NOT_FOUND", "技能包不存在")
        bundle = self._bundle(bundle_id, actor=actor)
        for record in records:
            if not self._can_see(record, actor):
                continue
            self.apply(record.skill_id, SkillAction.UNINSTALL, actor=actor)
        return bundle

    def _bundle(self, bundle_id: str, *, actor: SkillActor) -> SkillBundle:
        metas: list[SkillMetadata] = []
        name = ""
        scope = SkillScope.USER
        owner: str | None = None
        label = ""
        for record in self._records():
            if record.bundle_id != bundle_id or record.status == SkillStatus.UNINSTALLED:
                continue
            if not self._can_see(record, actor):
                continue
            name = name or (record.bundle_name or record.source_label)
            scope = record.scope
            owner = record.owner_id
            label = record.source_label
            metas.append(self._metadata(record))
        if not metas:
            raise SkillError("BUNDLE_NOT_FOUND", "技能包不存在")
        return SkillBundle(
            bundle_id=bundle_id,
            name=name,
            scope=scope,
            owner_id=owner,
            source_label=label,
            skill_count=len(metas),
            skills=tuple(metas),
        )

    def read_instructions(self, skill_id: str, version: str, *, actor: SkillActor) -> str:
        record = self._visible(skill_id, actor)
        if not any(item.version == version for item in record.versions):
            raise SkillError("SKILL_NOT_FOUND", "技能不存在")
        path = self._package(skill_id, version) / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        _manifest, body = parse_skill_markdown(text)
        return body

    def stored_digest(self, skill_id: str, version: str, *, actor: SkillActor) -> str:
        record = self._visible(skill_id, actor)
        for item in record.versions:
            if item.version == version:
                return item.instruction_sha256
        raise SkillError("SKILL_NOT_FOUND", "技能不存在")

    def package_dir(self, skill_id: str, version: str, *, actor: SkillActor) -> Path:
        self.stored_digest(skill_id, version, actor=actor)
        package = self._root / "packages" / skill_id / version
        base = (self._root / "packages").resolve()
        target = package.resolve()
        if os.path.commonpath([str(base), str(target)]) != str(base):
            raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
        return target

    def _owner(self, scope: SkillScope, actor: SkillActor) -> str | None:
        if scope == SkillScope.SYSTEM:
            if not self._allow_system:
                raise SkillScopeError("SKILL_SCOPE_UNAVAILABLE", "该作用域还不能注册技能")
            return None
        if not actor.user_id:
            raise SkillScopeError("SKILL_ACTOR_REQUIRED", "缺少用户身份")
        return actor.user_id

    def _conflict(self, scope: SkillScope, owner: str | None, name: str) -> _Record | None:
        for record in self._records():
            if record.status == SkillStatus.UNINSTALLED:
                continue
            if record.scope == scope and record.owner_id == owner and record.name == name:
                return record
        return None

    def _visible(self, skill_id: str, actor: SkillActor) -> _Record:
        record = self._load(skill_id)
        if record is None or not self._can_see(record, actor):
            raise SkillError("SKILL_NOT_FOUND", "技能不存在")
        return record

    def _can_see(self, record: _Record, actor: SkillActor) -> bool:
        if record.scope == SkillScope.SYSTEM:
            return True
        return record.owner_id is not None and record.owner_id == actor.user_id

    def _records(self) -> list[_Record]:
        found: list[_Record] = []
        for path in (self._root / "records").glob("sk_*.json"):
            record = self._read_record(path)
            if record is not None:
                found.append(record)
        return found

    def _load(self, skill_id: str) -> _Record | None:
        if not skill_id.startswith("sk_"):
            return None
        return self._read_record(self._root / "records" / f"{skill_id}.json")

    def _read_record(self, path: Path) -> _Record | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return _Record.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError):
            return None

    def _save(self, record: _Record) -> None:
        path = self._root / "records" / f"{record.skill_id}.json"
        self._ensure_inside(self._root / "records", path)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(record.model_dump_json(), encoding="utf-8")
        os.replace(temporary, path)

    def _write_package(self, record: _Record, instructions: str) -> None:
        package = self._package(record.skill_id, record.version)
        package.mkdir(parents=True, exist_ok=True)
        target = package / "SKILL.md"
        self._ensure_inside(package, target)
        target.write_text(_render(record, instructions), encoding="utf-8")

    def _package(self, skill_id: str, version: str) -> Path:
        package = self._root / "packages" / skill_id / version
        self._ensure_inside(self._root / "packages", package)
        return package

    def _copy_tree(self, source: Path, skill_id: str, version: str) -> None:
        package = self._package(skill_id, version)
        for path in source.rglob("*"):
            if not path.is_file() or path.name == "SKILL.md":
                continue
            relative = path.relative_to(source).as_posix()
            try:
                checked, _kind = normalize_resource_path(relative)
            except SkillValidationError:
                continue
            data = path.read_bytes()
            if len(data) > _MAX_RESOURCE:
                raise SkillValidationError("SKILL_RESOURCE_TOO_LARGE", "技能资源过大")
            target = package / checked
            self._ensure_inside(package, target)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def _ensure_inside(self, root: Path, child: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        parent = child.parent
        parent.mkdir(parents=True, exist_ok=True)
        base = root.resolve()
        target = child.resolve()
        if os.path.commonpath([str(base), str(target)]) != str(base):
            raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")

    def _check_body(self, manifest: SkillManifest, instructions: str) -> None:
        encoded = instructions.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        if digest != manifest.instruction_sha256 or len(encoded) != manifest.body_bytes:
            raise SkillValidationError("SKILL_DIGEST_MISMATCH", "技能正文和清单不一致")

    def _skill(self, record: _Record) -> Skill:
        return Skill(
            metadata=self._metadata(record),
            source=SkillSource(kind=record.source_kind, label=record.source_label),
            instruction_sha256=record.instruction_sha256,
            active_count=record.active_count,
        )

    def _metadata(self, record: _Record) -> SkillMetadata:
        return SkillMetadata(
            skill_id=record.skill_id,
            name=record.name,
            description=record.description,
            version=record.version,
            scope=record.scope,
            status=record.status,
            source=record.source_kind,
            owner_id=record.owner_id,
            license=record.license,
            compatibility=record.compatibility,
            category=record.category,
            tags=_tags(record.metadata),
            bundle_id=record.bundle_id,
            bundle_name=record.bundle_name,
        )


def _tags(metadata: dict[str, str]) -> tuple[str, ...]:
    raw = metadata.get("tags", "")
    found: list[str] = []
    for part in raw.split(","):
        tag = part.strip().lower()
        if tag and tag not in found and len(tag) <= 32 and tag.replace("-", "a").isalnum():
            found.append(tag)
        if len(found) == 8:
            break
    return tuple(found)


def _category(manifest: SkillManifest) -> str | None:
    raw = manifest.metadata.get("category")
    if raw is None:
        return None
    if len(raw) > 32 or not raw.replace("-", "a").isalnum() or raw != raw.lower():
        raise SkillValidationError("SKILL_CATEGORY_INVALID", "技能分类无效")
    return raw


def _render(record: _Record, body: str) -> str:
    lines = [
        "---",
        f"name: {record.name}",
        f"description: {record.description}",
        f"version: {record.version}",
    ]
    if record.license:
        lines.append(f"license: {record.license}")
    if record.compatibility:
        lines.append(f"compatibility: {record.compatibility}")
    if record.metadata:
        lines.append("metadata:")
        for key in sorted(record.metadata):
            lines.append(f"  {key}: {record.metadata[key]}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def _open_zip(payload: bytes) -> tuple[zipfile.ZipFile, dict[str, bytes]]:
    if len(payload) > _MAX_PACKAGE:
        raise SkillValidationError("SKILL_PACKAGE_TOO_LARGE", "技能包过大")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise SkillValidationError("SKILL_PACKAGE_INVALID", "技能包无效") from exc
    total = 0
    names: list[str] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        total += info.file_size
        names.append(info.filename)
    if total > _MAX_PACKAGE:
        archive.close()
        raise SkillValidationError("SKILL_PACKAGE_TOO_LARGE", "技能包过大")
    prefix = _zip_prefix(names)
    members: dict[str, bytes] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        relative = _zip_relative(info.filename, prefix)
        if relative is None:
            continue
        data = archive.read(info)
        if len(data) > _MAX_RESOURCE:
            archive.close()
            raise SkillValidationError("SKILL_RESOURCE_TOO_LARGE", "技能资源过大")
        members[relative] = data
    if "SKILL.md" not in members:
        archive.close()
        raise SkillValidationError("SKILL_PACKAGE_INVALID", "技能包缺少说明")
    return archive, members


def _zip_prefix(names: list[str]) -> str:
    cleaned = [name.replace("\\", "/") for name in names]
    if any(name == "SKILL.md" for name in cleaned):
        return ""
    tops = {name.split("/", 1)[0] for name in cleaned if "/" in name}
    if len(tops) == 1:
        return next(iter(tops)) + "/"
    return ""


def _zip_relative(name: str, prefix: str) -> str | None:
    normal = name.replace("\\", "/")
    if normal.startswith("/") or ":" in normal or "\x00" in normal:
        raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
    if prefix and normal.startswith(prefix):
        normal = normal[len(prefix) :]
    parts = [part for part in normal.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
    relative = "/".join(parts)
    if relative == "SKILL.md":
        return relative
    try:
        checked, _kind = normalize_resource_path(relative)
    except SkillValidationError:
        return None
    return checked


def _bundle_name(raw: str) -> str:
    """从上传文件名推导技能包展示名。"""
    name = raw.strip().replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"\.zip$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^A-Za-z0-9 _.\-\u4e00-\u9fff]+", "", name).strip()
    if len(name) > 64:
        name = name[:64].strip()
    if not name:
        raise SkillValidationError("SKILL_PACKAGE_INVALID", "技能包名称无效")
    return name


def _bundle_entry_path(name: str) -> str | None:
    normal = name.replace("\\", "/")
    if normal.startswith("/") or ":" in normal or "\x00" in normal:
        raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
    parts = [part for part in normal.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
    if not parts:
        return None
    return "/".join(parts)


def _extract_bundle(payload: bytes) -> Path:
    """把技能包 zip 安全解压到临时目录，返回目录路径（调用方负责清理）。"""
    if len(payload) > _MAX_PACKAGE:
        raise SkillValidationError("SKILL_PACKAGE_TOO_LARGE", "技能包过大")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise SkillValidationError("SKILL_PACKAGE_INVALID", "技能包无效") from exc
    base = Path(tempfile.mkdtemp(prefix="skill_bundle_"))
    try:
        total = 0
        for info in archive.infolist():
            total += info.file_size
            if total > _MAX_PACKAGE:
                raise SkillValidationError("SKILL_PACKAGE_TOO_LARGE", "技能包过大")
        base_resolved = base.resolve()
        for info in archive.infolist():
            if info.is_dir():
                continue
            relative = _bundle_entry_path(info.filename)
            if relative is None:
                continue
            data = archive.read(info)
            if len(data) > _MAX_RESOURCE:
                raise SkillValidationError("SKILL_RESOURCE_TOO_LARGE", "技能资源过大")
            target = base / Path(*relative.split("/"))
            if os.path.commonpath([str(base_resolved), str(target.resolve())]) != str(base_resolved):
                raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return base
    except Exception:
        shutil.rmtree(base, ignore_errors=True)
        raise
    finally:
        archive.close()
