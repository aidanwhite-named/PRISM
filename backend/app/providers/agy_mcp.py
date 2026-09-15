"""agy 전역 MCP 설정에 PRISM 검색 서버(prism-search)를 등록한다.

왜 전역 설정인가
----------------
Claude·Codex 는 실행 인자로 **그 실행에만** MCP 서버를 붙인다. agy 1.2.2 에는
그런 플래그가 없다. `agy mcp add` 는 ``~/.gemini/config/mcp_config.json`` 에만
쓰고, 실행 폴더의 ``.mcp.json`` · ``.agents/mcp_config.json`` 은 읽지 않는다
(2026-09-14 실측).

그래서 서버 정의만 전역에 한 번 두고, **실행별 값은 환경변수로 넘긴다.**
같은 날 탐침 서버로 확인했다 — agy 는 자기 환경을 MCP 자식 프로세스에
그대로 물려준다(부모에만 준 PROBE_TOKEN 이 서버에 도착했다).

지키는 선
---------
- 전역 설정에는 **비밀을 넣지 않는다.** EPO 키는 서버가 PRISM DB 에서 읽는다.
- 서버는 PRISM 이 넘긴 ``PRISM_SEARCH_WORK_DIR`` 가 없으면 도구를 하나도
  내놓지 않는다. 사용자가 터미널에서 여는 agy 나 PRISM 문서 분석 실행에는
  서버가 떠도 아무 도구가 없다.
- 권한 규칙은 ``mcp(prism-search/*)`` 하나만 넣는다. 없으면 headless 실행에서
  호출이 자동 거부된다.
- 기존 항목을 지우지 않는다. JSON 이 깨져 있으면 쓰지 않고 오류를 낸다.
  쓰기 전에 백업을 만들고 원자적으로 바꾼다(agy_permissions 와 같은 규칙).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import agy_permissions as perms

SERVER_NAME = "prism-search"
PERMISSION_RULE = f"mcp({SERVER_NAME}/*)"
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"

#: 실행마다 달라지는 값. 전역 설정이 아니라 agy 자식 환경으로만 넘긴다.
RUN_ENV_KEYS = (
    "PRISM_SEARCH_WORK_DIR",
    "PRISM_DATA_DIR",
    "PRISM_SEARCH_CUTOFF",
    "PRISM_SEARCH_MAX_TOOL_CALLS",
)

_CONFIG_OVERRIDE = "PRISM_AGY_MCP_CONFIG_PATH"
_SCHEMA_OVERRIDE = "PRISM_AGY_MCP_SCHEMA_DIR"


def config_path() -> Path:
    override = os.environ.get(_CONFIG_OVERRIDE)
    if override:
        return Path(override)
    return Path.home() / ".gemini" / "config" / "mcp_config.json"


def schema_dir() -> Path:
    """agy 가 서버별 도구 스키마를 풀어 두는 곳.

    실측: 모델은 MCP 도구를 부르기 전에 ``<이 폴더>/<도구>.json`` 을 view_file 로
    읽는다. 이 읽기는 본문 열람이 아니라 도구 설명 확인이다.
    """
    override = os.environ.get(_SCHEMA_OVERRIDE)
    if override:
        return Path(override)
    return Path.home() / ".gemini" / "antigravity-cli" / "mcp" / SERVER_NAME


def desired_entry() -> dict:
    backend_root = Path(__file__).resolve().parents[2]
    return {
        "command": sys.executable,
        "args": ["-m", "app.search_mcp_server"],
        "env": {"PYTHONPATH": str(backend_root)},
        "disabled": False,
    }


@dataclass(frozen=True)
class Registration:
    registered: bool = False
    permitted: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.registered and self.permitted and not self.error

    def detail(self) -> str:
        if self.error:
            return self.error
        missing = []
        if not self.registered:
            missing.append(f"{config_path()} 에 {SERVER_NAME} 서버")
        if not self.permitted:
            missing.append(f"{perms.settings_path()} 에 {PERMISSION_RULE} 규칙")
        return "agy 설정에 없음: " + ", ".join(missing) if missing else ""


def _load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise perms.AgyPermissionsError(f"agy MCP 설정을 읽지 못했습니다: {path} ({exc})") from exc
    # agy 는 서버가 하나도 없을 때 0 바이트 파일을 만든다(실측). 빈 설정이다.
    if not raw.strip():
        return {}
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise perms.AgyPermissionsError(
            f"agy MCP 설정이 올바른 JSON 이 아닙니다: {path} ({exc.lineno}행 {exc.colno}열). "
            "덮어쓰지 않았습니다."
        ) from exc
    if not isinstance(document, dict):
        raise perms.AgyPermissionsError(f"agy MCP 설정의 최상위가 객체가 아닙니다: {path}")
    servers = document.get("mcpServers")
    if servers is not None and not isinstance(servers, dict):
        raise perms.AgyPermissionsError(f"agy MCP 설정의 mcpServers 가 객체가 아닙니다: {path}")
    return document


def _allow_rules(document: dict, path: Path) -> list:
    permissions = document.get("permissions")
    if permissions is None:
        return []
    if not isinstance(permissions, dict):
        raise perms.AgyPermissionsError(f"agy 설정의 permissions 가 객체가 아닙니다: {path}")
    rules = permissions.get("allow")
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise perms.AgyPermissionsError(f"agy 설정의 permissions.allow 가 배열이 아닙니다: {path}")
    return rules


def read_state() -> Registration:
    """쓰지 않고 지금 상태만 본다. 예외를 올리지 않는다."""
    try:
        servers = _load_config(config_path()).get("mcpServers") or {}
        registered = servers.get(SERVER_NAME) == desired_entry()
        settings = perms.settings_path()
        document = perms._load(settings) if settings.exists() else {}
        permitted = PERMISSION_RULE in _allow_rules(document, settings)
        return Registration(registered=registered, permitted=permitted)
    except perms.AgyPermissionsError as exc:
        return Registration(error=str(exc))


def ensure_registered() -> Registration:
    """서버 정의와 권한 규칙을 병합한다. 이미 맞으면 아무것도 쓰지 않는다."""
    try:
        path = config_path()
        document = _load_config(path)
        servers = document.setdefault("mcpServers", {})
        wanted = desired_entry()
        if servers.get(SERVER_NAME) != wanted:
            servers[SERVER_NAME] = wanted
            if path.exists() and path.stat().st_size:
                perms._backup(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            perms._atomic_write(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")

        settings = perms.settings_path()
        document = perms._load(settings) if settings.exists() else {}
        rules = _allow_rules(document, settings)
        if PERMISSION_RULE not in rules:
            document.setdefault("permissions", {})["allow"] = [*rules, PERMISSION_RULE]
            if settings.exists():
                perms._backup(settings)
            settings.parent.mkdir(parents=True, exist_ok=True)
            perms._atomic_write(settings, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    except (perms.AgyPermissionsError, OSError) as exc:
        return Registration(error=str(exc))
    return read_state()


def run_env(mcp_servers: dict | None) -> dict[str, str]:
    """runner 가 만든 prism-search 서버 정의에서 실행별 환경변수만 꺼낸다."""
    server = (mcp_servers or {}).get(SERVER_NAME)
    env = server.get("env") if isinstance(server, dict) else None
    if not isinstance(env, dict):
        return {}
    return {key: str(env[key]) for key in RUN_ENV_KEYS if key in env}


def is_schema_dir(path: str) -> bool:
    """이 경로가 prism-search 스키마 폴더 그 자체인가. 상위·하위·형제 폴더는 아니다."""
    if not path:
        return False
    try:
        target = Path(path).resolve()
        root = schema_dir().resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    return str(target).casefold() == str(root).casefold()


def schema_read_tool(path: str) -> str | None:
    """이 경로가 prism-search 도구 스키마 파일이면 도구 이름."""
    if not path:
        return None
    try:
        target = Path(path).resolve()
        root = schema_dir().resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if target.suffix.casefold() != ".json":
        return None
    if str(target.parent).casefold() != str(root).casefold():
        return None
    return target.stem
