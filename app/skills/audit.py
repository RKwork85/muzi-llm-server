"""技能安全审核。落盘后异步判别 Prompt 注入、窃取凭据、恶意挖矿等。

默认用无 LLM 依赖的静态规则审核器（宁可误杀也不漏放），也可注入调用
廉价 LLM 的审核器。审核通过 -> ENABLED，不通过 -> REJECTED。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol

from app.skills.models import SkillActor
from app.skills.registry import FileSkillRegistry

logger = logging.getLogger("muzi_llm")


@dataclass(frozen=True)
class AuditVerdict:
    safe: bool
    category: str = ""
    reason: str = ""


class SkillAuditor(Protocol):
    async def audit(self, body: str, scripts: list[tuple[str, str]]) -> AuditVerdict:
        """对正文与脚本内容给出安全判定。"""


class StaticPatternAuditor:
    """静态规则审核。无网络、无 LLM，毫秒级完成，适合作为默认兜底。"""

    _INJECTION = (
        re.compile(r"ignore\s+(?:all\s+)?(?:the\s+)?(?:above|previous|prior|earlier)\s+instructions?", re.IGNORECASE),
        re.compile(r"忽略(?:所有|一切|任何)?(?:系统)?(?:的)?(?:之前|以上|上面|前述|上述)?(?:所有)?(?:指令|提示词|规则|设定)", re.IGNORECASE),
        re.compile(r"disregard\s+(?:any\s+|all\s+)?(?:prior\s+)?instructions?", re.IGNORECASE),
        re.compile(r"override\s+(?:the\s+)?system\s+prompt", re.IGNORECASE),
        re.compile(r"无视(?:系统)?(?:提示词|指令|规则)", re.IGNORECASE),
    )
    _CREDENTIAL = (
        # 整体读取/外发环境变量。os.environ.copy() 是给子进程传环境的合法用法，排除之。
        re.compile(r"os\.environ(?!\.copy\s*\()|process\.env", re.IGNORECASE),
        # 读取单个「敏感名称」的环境变量；os.getenv("HOME") 这类无害用法不误杀。
        re.compile(
            r"(?:os\.environ\s*\[\s*['\"]|os\.environ\.get\s*\(\s*['\"]|os\.getenv\s*\(\s*['\"]|getenv\s*\(\s*['\"])"
            r"[\w-]*(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|credential|private[_-]?key)",
            re.IGNORECASE,
        ),
        re.compile(r"\.aws[\\/]credentials|id_rsa|id_ed25519|\.netrc|\bnpmrc\b", re.IGNORECASE),
        re.compile(r"(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{8,}", re.IGNORECASE),
    )
    _MINING = (
        re.compile(r"xmrig|stratum|monero|coinhive|minergate|nicehash", re.IGNORECASE),
        re.compile(r"crypto(?:currency)?\s*min(?:e|ing)", re.IGNORECASE),
    )

    async def audit(self, body: str, scripts: list[tuple[str, str]]) -> AuditVerdict:
        for pattern in self._INJECTION:
            if pattern.search(body):
                return AuditVerdict(False, "prompt_injection", "正文疑似含越权覆盖系统提示的指令")
        texts = [(name, text) for name, text in scripts]
        for rel, text in texts:
            for pattern in self._INJECTION:
                if pattern.search(text):
                    return AuditVerdict(False, "prompt_injection", f"脚本 {rel} 疑似含提示注入")
        for rel, text in texts:
            for pattern in self._CREDENTIAL:
                if pattern.search(text):
                    return AuditVerdict(False, "credential_theft", f"脚本 {rel} 疑似读取或外发敏感凭据")
            for pattern in self._MINING:
                if pattern.search(text):
                    return AuditVerdict(False, "crypto_mining", f"脚本 {rel} 疑似挖矿")
        return AuditVerdict(True)


class SkillAuditService:
    def __init__(self, registry: FileSkillRegistry, auditor: SkillAuditor | None = None) -> None:
        self._registry = registry
        self._auditor: SkillAuditor = auditor or StaticPatternAuditor()

    async def audit_and_settle(self, skill_id: str, *, actor: SkillActor) -> AuditVerdict:
        """审核一个 REGISTERED 技能：通过则启用，不通过则拒绝。

        审核本身失败时默认拒绝（安全优先），不让未审核技能进入可用态。
        """
        try:
            body, scripts = self._collect(skill_id, actor)
            verdict = await self._auditor.audit(body, scripts)
        except Exception:
            logger.exception("skill audit collect failed skill=%s", skill_id)
            self._safe_reject(skill_id, actor)
            raise
        if verdict.safe:
            try:
                self._registry.enable(skill_id, actor=actor)
            except Exception:
                logger.exception("skill audit enable failed skill=%s", skill_id)
                raise
        else:
            self._safe_reject(skill_id, actor)
            logger.info(
                "skill audit rejected skill=%s category=%s reason=%s",
                skill_id,
                verdict.category,
                verdict.reason,
            )
        return verdict

    def _collect(self, skill_id: str, actor: SkillActor) -> tuple[str, list[tuple[str, str]]]:
        skill = self._registry.get(skill_id, actor=actor)
        version = skill.metadata.version
        body = self._registry.read_instructions(skill_id, version, actor=actor)
        package = self._registry.package_dir(skill_id, version, actor=actor)
        scripts: list[tuple[str, str]] = []
        for path in sorted(package.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in (".py", ".js"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scripts.append((path.relative_to(package).as_posix(), text))
        return body, scripts

    def _safe_reject(self, skill_id: str, actor: SkillActor) -> None:
        try:
            self._registry.reject(skill_id, actor=actor)
        except Exception:
            logger.exception("skill audit reject failed skill=%s", skill_id)
