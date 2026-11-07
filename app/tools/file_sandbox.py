"""把用户传入的相对路径限制在工作区根目录内。"""

from __future__ import annotations

from pathlib import Path

MAX_PATH_CHARS = 240
MAX_FILE_BYTES = 256 * 1024
MAX_SCAN_FILES = 200
MAX_DIRECTORY_DEPTH = 8
MAX_SNIPPET_CHARS = 200

_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_BLOCKED_DIRS = frozenset({"node_modules", "__pycache__"})
_SENSITIVE_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".kdbx")


class FileDenied(Exception):
    """路径不存在、越界或命中拒绝规则。外部表现必须相同。"""


class FileTooLarge(Exception):
    """普通文本超出字节上限。"""


def confined(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def blocked_name(part: str) -> bool:
    if not part or part in {".", ".."} or part.startswith("."):
        return True
    lowered = part.lower()
    if lowered in _BLOCKED_DIRS:
        return True
    stem = lowered.split(".", 1)[0].rstrip(" ")
    if stem.upper() in _RESERVED:
        return True
    if lowered.startswith(("id_rsa", "credentials", "secrets")):
        return True
    return lowered.endswith(_SENSITIVE_SUFFIXES)


def resolve_inside(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or "\x00" in raw or ":" in raw or len(raw) > MAX_PATH_CHARS:
        raise FileDenied()
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise FileDenied()
    base = root.resolve()
    if raw == ".":
        return base
    path = Path(raw)
    if path.is_absolute() or bool(path.drive) or bool(path.anchor) or not path.parts:
        raise FileDenied()
    for part in path.parts:
        if blocked_name(part):
            raise FileDenied()
    try:
        resolved = base.joinpath(*path.parts).resolve()
    except OSError as exc:
        raise FileDenied() from exc
    if not confined(base, resolved):
        raise FileDenied()
    return resolved


def display_path(root: Path, resolved: Path) -> str:
    relative = resolved.resolve().relative_to(root.resolve())
    text = relative.as_posix()
    return "." if text == "" else text


def load_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FileDenied() from exc
    if size > MAX_FILE_BYTES:
        raise FileTooLarge()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise FileDenied() from exc
    if len(data) > MAX_FILE_BYTES:
        raise FileTooLarge()
    if b"\x00" in data:
        raise FileDenied()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FileDenied() from exc
