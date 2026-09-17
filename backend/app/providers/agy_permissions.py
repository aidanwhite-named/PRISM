"""이전 검색 방식의 agy 페이지 열람 권한 읽기 및 MCP 설정 파일 공통 입출력."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# 규칙 문법: read_url(<host>).
_RULE = re.compile(r"^\s*read_url\s*\(\s*([^)\s]+)\s*\)\s*$", re.IGNORECASE)

# read_url(*) 의 호스트 자리. 들어 있으면 모든 주소를 열 수 있다.
WILDCARD = "*"
_ENV_OVERRIDE = "PRISM_AGY_SETTINGS_PATH"


class AgyPermissionsError(Exception):
    """설정 파일을 안전하게 읽거나 쓸 수 없다. 이때는 손대지 않는다."""


def settings_path() -> Path:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override)
    return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def _host_of(rule: object) -> str:
    match = _RULE.match(str(rule or ""))
    return match.group(1).lower() if match else ""


@dataclass(frozen=True)
class AgyPermissionState:
    """이전 검색 방식의 프롬프트에 전달할 허용 목록 상태."""

    path: str
    exists: bool
    #: read_url 규칙에서 뽑은 호스트 전부. 사용자가 직접 넣은 것을 포함한다.
    allowed_hosts: tuple[str, ...] = ()
    #: read_url(*) 가 들어 있는가. 참이면 호스트 목록과 무관하게 모든 주소가 열린다.
    wildcard: bool = False
    #: 읽지 못한 이유. 비어 있지 않으면 다른 칸은 신뢰할 수 없다.
    error: str = ""


def _state_from(path: Path, document: dict) -> AgyPermissionState:
    permissions = document.get("permissions")
    raw_rules = permissions.get("allow") if isinstance(permissions, dict) else None
    rules = raw_rules if isinstance(raw_rules, list) else []
    hosts: list[str] = []
    for rule in rules:
        host = _host_of(rule)
        if host and host not in hosts:
            hosts.append(host)
    known = set(hosts)
    return AgyPermissionState(
        path=str(path),
        exists=True,
        allowed_hosts=tuple(hosts),
        wildcard=WILDCARD in known,
    )


def _load(path: Path) -> dict:
    """설정 파일을 읽는다. 깨져 있으면 올린다 — 덮어쓰지 않기 위해서다."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgyPermissionsError(
            f"agy 설정 파일을 읽지 못했습니다: {path} ({exc.strerror or exc})"
        ) from exc
    if not raw.strip():
        raise AgyPermissionsError(
            f"agy 설정 파일이 비어 있습니다: {path}. PRISM 이 임의로 새로 만들지 "
            "않았습니다. 파일을 확인한 뒤 다시 적용하십시오."
        )
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgyPermissionsError(
            f"agy 설정 파일이 올바른 JSON 이 아닙니다: {path} "
            f"({exc.lineno}행 {exc.colno}열: {exc.msg}). 손상된 파일을 덮어쓰지 "
            "않았습니다. 직접 고친 뒤 다시 적용하십시오."
        ) from exc
    if not isinstance(document, dict):
        raise AgyPermissionsError(
            f"agy 설정 파일의 최상위가 객체가 아닙니다: {path}. 덮어쓰지 않았습니다."
        )
    return document


def read_state() -> AgyPermissionState:
    """지금 상태를 읽는다. 아무것도 쓰지 않는다.

    예외를 올리지 않는다 — 이 함수는 이전 검색 실행 경로가 부르고,
    "허용 목록을 못 읽었다"가 실행을 멈출 이유는 아니기 때문이다. 실패는
    error 칸에 담아 그대로 보여 준다.
    """
    path = settings_path()
    if not path.exists():
        return AgyPermissionState(path=str(path), exists=False)
    try:
        return _state_from(path, _load(path))
    except AgyPermissionsError as exc:
        return AgyPermissionState(path=str(path), exists=True, error=str(exc))


def allowed_hosts() -> tuple[str, ...]:
    """이 실행에서 실제로 열 수 있는 호스트. 읽지 못하면 빈 튜플.

    빈 튜플은 "제한 없음"이 아니라 "하나도 열 수 없다"로 읽어야 한다. 프롬프트도
    그렇게 말한다 — 모르는 상태에서 열어 보게 하면 그 한 번의 거부로 실행 전체가
    사라진다.
    """
    return read_state().allowed_hosts


def _backup(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.name}.prism-backup-{stamp}")
    counter = 1
    while target.exists():
        counter += 1
        target = path.with_name(f"{path.name}.prism-backup-{stamp}-{counter}")
    target.write_bytes(path.read_bytes())
    return target


def _atomic_write(path: Path, text: str) -> None:
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
