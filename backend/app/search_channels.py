"""Tool availability, not independent channel execution or candidate policy."""
from __future__ import annotations
import ast
import contextlib
from datetime import datetime, timedelta, timezone
from .patent_search import describe

STATUS_LABELS = {
    "available": "사용 가능", "disabled": "연동 꺼짐",
    "not_configured": "인증 미설정", "not_implemented": "접속 미구현",
    "unsupported_transport": "이 Provider의 실행별 MCP 연결 미지원",
    "not_registered": "agy 전역 MCP 설정에 PRISM 검색 서버 미등록",
    "unverified": "연결 미확인", "unreachable": "연결 실패",
}

# web 채널의 실측 기록이 담긴 설정 키. Provider id 로 나눈다 — 도구가 Provider
# 안에 있으므로 한쪽이 살아 있어도 다른 쪽은 죽어 있을 수 있다.
WEB_HEALTH_KEY = "web_search_health"

# 실측 기록의 유효 기간. **성공도 만료시킨다.**
#
# 2026-09-05 실행은 search_web 7회에 실패 0이었고, 같은 Provider·같은 모델의
# 2026-09-12 실행은 4회 전부 실패했다. 그 사이 바뀐 것은 agy CLI 뿐이다
# (1.2.2 로 갱신). 지난주 성공을 근거로 이번 주에도 "사용 가능"을 띄우면
# 그것이 바로 없애려는 거짓말이다. 기록은 늙으면 모름으로 돌아간다.
WEB_HEALTH_TTL = timedelta(hours=6)

_SOURCE_LABELS = {"run": "검색 실행 중 관측", "check": "검색 도구 확인"}
_CHECK_HINT = "설정 화면에서 「검색 도구 확인」을 누르면 실제로 한 번 불러 확인합니다."


def _parse_moment(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _failure_text(error) -> str:
    """Provider 마다 다른 오류 표현에서 사람이 읽을 한 줄을 뽑는다.

    agy 는 오류 dict 를 str() 로 눌러서 넘긴다(agy_stream). 그대로 보이면
    사용자가 읽는 것은 "{'type': 'TOOL_ERROR', 'message': ...}" 라는 파이썬
    repr 이다. 한 번 더 풀어서 사유 문장만 남긴다.
    """
    if isinstance(error, str) and error.startswith("{"):
        with contextlib.suppress(ValueError, SyntaxError):
            error = ast.literal_eval(error)
    if isinstance(error, dict):
        error = error.get("message") or error.get("type") or error
    return str(error or "").strip()[:300]


def web_status(record, *, cli_version: str = "", now: datetime | None = None) -> dict:
    """web 채널은 **실측으로만** 사용 가능이 된다.

    예전에는 상수였다. 그래서 2026-09-12 실행은 search_web 네 번이 모두
    TOOL_ERROR 로 죽었는데도 같은 보고서 안에 "web: 사용 가능"이라고 적었다.
    도구가 살았는지 확인하는 코드가 어디에도 없는 채로 살았다고 말한 것이다.

    확인한 적이 없으면 없다고 말한다. 모르는 것을 사용 가능 쪽으로 올려 적지
    않는다 — 그래야 0건 보고서를 받은 사람이 "관련 문헌이 없다"와 "도구가
    죽었다"를 구분할 수 있다.
    """
    if not isinstance(record, dict) or record.get("ok") is None:
        if isinstance(record, dict) and record.get("completed_calls"):
            seen = _parse_moment(record.get("at"))
            fresh = seen and timedelta(0) <= (now or datetime.now(timezone.utc)) - seen <= WEB_HEALTH_TTL
            same_cli = not cli_version or not record.get("cli_version") or cli_version == record["cli_version"]
            if not fresh or not same_cli:
                return {"status": "unverified", "detail": f"이전 호출 완료 기록은 만료되었거나 CLI가 바뀌었습니다. {_CHECK_HINT}"}
            return {"status": "unverified", "detail": (
                f"{record['completed_calls']}회 호출 완료를 관측했습니다. "
                "Provider가 성공 여부를 제공하지 않아 결과 성공은 미확인입니다. 웹 검색은 시도할 수 있습니다.")}
        return {"status": "unverified", "detail": f"확인 기록이 없습니다. {_CHECK_HINT}"}
    observed = _parse_moment(record.get("at"))
    if observed is None:
        return {"status": "unverified", "detail": f"확인 시각을 읽지 못했습니다. {_CHECK_HINT}"}

    seen = _stamp(observed)
    recorded_cli = str(record.get("cli_version") or "").strip()
    if cli_version and recorded_cli and cli_version != recorded_cli:
        return {"status": "unverified", "detail": (
            f"CLI 가 {recorded_cli} → {cli_version} 로 바뀐 뒤 확인하지 않았습니다. "
            f"마지막 확인 {seen}. {_CHECK_HINT}")}
    hours = int(WEB_HEALTH_TTL.total_seconds() // 3600)
    if (now or datetime.now(timezone.utc)) - observed > WEB_HEALTH_TTL:
        return {"status": "unverified", "detail": (
            f"마지막 확인 {seen} 에서 {hours}시간이 지났습니다. {_CHECK_HINT}")}

    source = _SOURCE_LABELS.get(str(record.get("source") or ""), "실측")
    if record.get("ok"):
        return {"status": "available", "detail": f"{seen} {source}에서 응답했습니다."}
    detail = _failure_text(record.get("detail"))
    return {"status": "unreachable", "detail": (
        f"{seen} {source}에서 실패했습니다" + (f": {detail}" if detail else "") +
        ". 지금 실행하면 웹 채널에서는 아무것도 찾지 못합니다.")}


def web_evidence(tool_calls, tool_names, *, cli_version: str = "",
                 source: str = "run", now: datetime | None = None) -> dict | None:
    """실제 호출 기록에서 web 채널의 도달성 증거를 만든다.

    호출이 없거나 성패를 알 수 없으면 None 을 돌려준다. 증거가 없는데 기록을
    남기면 그것도 같은 종류의 거짓말이 된다 — 다음 화면이 그 기록을 실측으로
    읽기 때문이다.
    """
    names = {str(name) for name in (tool_names or ())}
    calls = [call for call in (tool_calls or []) if str(call.get("name")) in names]
    outcomes = [call.get("ok") for call in calls]
    if True in outcomes:
        ok, detail = True, ""
    elif False in outcomes:
        failed = next(call for call in calls if call.get("ok") is False)
        ok, detail = False, _failure_text(failed.get("error"))
    else:
        completed = sum(call.get("completed") is True for call in calls)
        if not completed:
            return None
        return {"ok": None, "completed_calls": completed, "detail": "Completion observed; result success unknown",
                "source": source, "cli_version": str(cli_version or ""), "calls": len(calls),
                "at": (now or datetime.now(timezone.utc)).isoformat()}
    return {"ok": ok, "detail": detail, "source": source,
            "cli_version": str(cli_version or ""), "calls": len(calls),
            "at": (now or datetime.now(timezone.utc)).isoformat()}


def availability(values: dict, provider: str = "claude") -> dict:
    result = {"web": {"status": "available", "detail": "Provider 기본 웹 도구"}}
    registration = None
    for name in ("epo", "kiwee", "literature"):
        status = describe(values, name)
        code = "available"
        if not status.enabled:
            code = "disabled"
        elif name == "kiwee":
            code = "not_implemented"
        elif not status.configured:
            code = "not_configured"
        elif provider == "agy":
            if registration is None:
                registration = _agy_registration()
            if not registration.ok:
                code = "not_registered"
                result[name] = {"status": code, "detail": registration.detail() or STATUS_LABELS[code]}
                continue
        elif provider not in ("claude", "codex"):
            code = "unsupported_transport"
        result[name] = {"status": code, "detail": STATUS_LABELS[code]}
    return result


def _agy_registration():
    # agy 는 실행별 MCP 인자가 없어 전역 설정 등록 여부로 판정한다.
    from .providers import agy_mcp
    return agy_mcp.read_state()


def mcp_transport_ready(provider: str) -> bool:
    if provider in ("claude", "codex"):
        return True
    return provider == "agy" and _agy_registration().ok

def no_usable_channel(statuses: dict) -> bool:
    """쓸 수 있는 채널이 하나도 없는가.

    조건을 좁게 잡는다. web 이 "확인 안 됨"인 것만으로는 참이 아니다 — 모르는
    것을 이유로 실행을 막으면, 아직 한 번도 확인하지 않은 설치에서는 검색을
    아예 시작할 수 없다. 실측으로 죽은 것을 확인했을 때만 막는다.
    """
    web = (statuses.get("web") or {}).get("status")
    return web == "unreachable" and all(
        status.get("status") != "available" for status in statuses.values()
    )


def unusable_channel_message(statuses: dict) -> str:
    """차단 사유와 **빠져나갈 길**을 함께 적는다.

    사유만 적으면 사용자는 막다른 골목에 선다. 실측 기록은 WEB_HEALTH_TTL 동안
    유효하므로, 그 사이에 도구가 복구되어도 기록은 실패로 남아 있다. 다시
    확인하는 방법을 알려주지 않으면 그 시간은 손쓸 수 없는 시간이 된다.
    """
    web = statuses.get("web") or {}
    others = ", ".join(
        f"{name} {STATUS_LABELS.get(status.get('status'), status.get('status'))}"
        for name, status in statuses.items() if name != "web"
    )
    return (
        "쓸 수 있는 검색 채널이 없습니다. "
        + str(web.get("detail") or "")
        + f" 다른 채널도 모두 닫혀 있습니다: {others}. "
        "실행해도 검색 없이 모델의 기억으로 후보가 만들어지므로 시작하지 "
        "않았고, 토큰은 소모되지 않았습니다. 다음 중 하나를 하십시오. "
        "(1) Provider 를 claude 또는 codex 로 바꾸면 EPO 와 비특허문헌 채널이 "
        "열립니다. (2) 웹 검색 도구가 복구됐다면 설정 화면에서 「검색 도구 "
        "확인」을 다시 누르십시오 — 확인이 성공하면 즉시 실행할 수 있습니다."
    )


def available_mcp_names(statuses: dict) -> tuple[str, ...]:
    names = ["mcp__prism-search__search_capabilities"]
    for name in ("epo", "kiwee", "literature"):
        if statuses.get(name, {}).get("status") == "available":
            names += [f"mcp__prism-search__{name}_search", f"mcp__prism-search__{name}_fetch"]
    return tuple(names)

def cell(value) -> str:
    import html
    text = html.escape(str(value or ""), quote=True)
    for char in ("\\", "|", "*", "_", "[", "]", "`"):
        text = text.replace(char, "\\" + char)
    return text.replace("\r", " ").replace("\n", " ")

DEPTH_LIMITS = {"quick": (15, 300), "standard": (40, 900), "deep": (80, 1800)}
def execution_limits(values: dict, depth: str = "standard") -> tuple[int, int]:
    """Presets only bound total calls/time, never channels or candidates."""
    calls, seconds = DEPTH_LIMITS.get(depth, DEPTH_LIMITS["standard"])
    return (min(calls, max(1, int(values.get("max_search_tool_calls", 40)))),
            min(seconds, max(1, int(values.get("default_timeout_seconds", 900)))))
