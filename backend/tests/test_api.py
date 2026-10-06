"""API 계층: 프롬프트 CRUD, 업로드, 작업 실행, 이력, 설정."""

from __future__ import annotations

import json

import pytest

from .conftest import wait_for_job
from .pdf_fixture import build_pdf, build_scanned_like_pdf
from app.config import PATHS
from .conftest import _TEST_PROMPT_DIR
from pathlib import Path
PROMPT_DIR = Path(_TEST_PROMPT_DIR)


@pytest.fixture()
def prompt(client, monkeypatch):
    # Isolate legacy response/numbering compatibility from staged V5 comparison.
    # V5 rendering, persistence and semantic review are tested separately.
    from dataclasses import asdict, replace
    from app.task_instructions import ANALYSIS
    from app.analysis_protocol import INSTRUCTIONS
    from app.api import jobs
    legacy = replace(ANALYSIS, body='청구항과 인용발명을 대비하십시오.\n' + INSTRUCTIONS)
    monkeypatch.setattr(jobs, 'ANALYSIS', legacy)
    return asdict(legacy)


def citation_batch(client, filename: str = "citation.txt") -> str:
    """분석 실행을 만들기 위한 최소 첨부.

    구성대비 분석은 인용발명 문헌 없이는 시작할 수 없다. 문헌 내용을 보지 않는
    테스트도 실행을 만들려면 한 건은 올려야 한다.
    """
    response = client.post(
        "/api/uploads",
        files=[("files", (filename, b"citation document", "text/plain"))],
    )
    assert response.status_code == 200, response.text
    return response.json()["batch_id"]


# ---------------------------------------------------------------- prompts


def test_health(client) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


















# --------------------------------------------------------------- providers


def test_provider_list_reports_usability(client) -> None:
    providers = client.get("/api/providers").json()["providers"]
    by_id = {p["provider"]: p for p in providers}
    assert "mock" not in by_id
    for pid in ("agy", "claude", "codex"):
        assert pid in by_id
        assert "install_hint" in by_id[pid]


def test_unknown_provider_404(client) -> None:
    assert client.get("/api/providers/nope").status_code == 404


# ---------------------------------------------------------------- uploads


def test_upload_analysis_and_blocking(client) -> None:
    files = [
        ("files", ("ok.txt", b"content here", "text/plain")),
        ("files", ("doc.pdf", build_pdf(["Page one body text."]), "application/pdf")),
        ("files", ("bad.exe", b"MZ\x00\x00", "application/octet-stream")),
        ("files", ("CLAUDE.md", b"# config", "text/markdown")),
        ("files", ("../up.txt", b"traversal", "text/plain")),
    ]
    data = client.post("/api/uploads", files=files).json()
    accepted = {f["original_filename"]: f for f in data["files"]}
    rejected = {r["filename"] for r in data["rejected"]}

    assert accepted["ok.txt"]["delivery_mode"] == "DELIVERED_AS_INLINE_CONTEXT"
    assert accepted["doc.pdf"]["page_count"] == 1
    assert {"bad.exe", "CLAUDE.md", "../up.txt"} <= rejected


def test_upload_requires_files(client) -> None:
    assert client.post("/api/uploads", files=[]).status_code in (400, 422)


def test_upload_preserves_application_and_citation_roles(client) -> None:
    files = [
        ("files", ("application.txt", b"claim body", "text/plain")),
        ("files", ("citation.txt", b"prior art body", "text/plain")),
    ]
    response = client.post(
        "/api/uploads",
        files=files,
        data={"roles": json.dumps(["APPLICATION", "CITATION"])},
    )
    assert response.status_code == 200
    assert [item["role"] for item in response.json()["files"]] == [
        "APPLICATION",
        "CITATION",
    ]


def test_upload_rejects_mismatched_roles(client) -> None:
    response = client.post(
        "/api/uploads",
        files=[("files", ("one.txt", b"body", "text/plain"))],
        data={"roles": json.dumps(["APPLICATION", "CITATION"])},
    )
    assert response.status_code == 400


# ------------------------------------------------------------------- jobs


def test_job_success_flow(client, prompt) -> None:
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1. 테스트 청구항",
            "batch_id": citation_batch(client),
        },
    ).json()
    final = wait_for_job(client, job["id"])

    assert final["status"] == "SUCCEEDED", final.get('errors')
    assert final["result_text"]
    assert final["final_prompt_sha256"]
    assert final["prompt_snapshot"] == prompt["body"]
    assert "prompt_version" not in final
    assert final["duration_ms"] is not None


def test_claim_and_document_roles_reach_final_prompt(client, prompt) -> None:
    upload = client.post(
        "/api/uploads",
        files=[
            ("files", ("application.txt", b"application document", "text/plain")),
            ("files", ("citation.txt", b"citation document", "text/plain")),
        ],
        data={"roles": json.dumps(["APPLICATION", "CITATION"])},
    ).json()
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1. 전용 청구항 표식",
            "batch_id": upload["batch_id"],
        },
    ).json()
    final = wait_for_job(client, job["id"])
    prompt_text = client.get(f"/api/jobs/{job['id']}/final-prompt").text

    assert final["claim_text"] == "청구항 1. 전용 청구항 표식"
    assert {
        item["original_filename"]: item["role"] for item in final["attachments"]
    } == {
        "application.txt": "APPLICATION",
        "citation.txt": "CITATION",
    }
    assert "[출원발명 청구항]" in prompt_text
    assert "[출원발명 문서]" in prompt_text
    assert "[인용발명 문헌]" in prompt_text


def test_job_snapshot_survives_instruction_update(client, monkeypatch):
    from dataclasses import replace
    from app.api import jobs
    from app.task_instructions import ANALYSIS
    job = client.post('/api/jobs', json={
        'provider': 'test', 'claim_text': '청구항 1. 테스트 청구항',
        'batch_id': citation_batch(client),
    }).json()
    wait_for_job(client, job['id'])
    monkeypatch.setattr(jobs, 'ANALYSIS', replace(ANALYSIS, body='new instructions'))
    stored = client.get(f"/api/history/{job['id']}").json()
    assert stored['prompt_snapshot'] == ANALYSIS.body
    assert stored['prompt_name'] == ANALYSIS.name


@pytest.mark.parametrize(
    ("keyword", "status", "code"),
    [
        ("TEST_FAIL", "FAILED", "PROCESS_ERROR"),
        ("TEST_EMPTY", "FAILED", "EMPTY_RESULT"),
        ("TEST_AUTH", "FAILED", "AUTH_REQUIRED"),
        ("TEST_RATELIMIT", "FAILED", "RATE_LIMITED"),
    ],
)
def test_job_failure_paths(client, prompt, keyword, status, code) -> None:
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": keyword,
            "batch_id": citation_batch(client),
        },
    ).json()
    final = wait_for_job(client, job["id"])
    assert final["status"] == status
    assert final["error_code"] == code


def test_required_attachment_failure_fails_job(client, prompt) -> None:
    upload = client.post(
        "/api/uploads",
        files=[("files", ("scan.pdf", build_scanned_like_pdf(2), "application/pdf"))],
    ).json()
    attachment_id = upload["files"][0]["attachment_id"]
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1. 테스트 청구항",
            "batch_id": upload["batch_id"],
            "required_map": {attachment_id: True},
        },
    ).json()
    final = wait_for_job(client, job["id"])
    assert final["status"] == "FAILED"
    assert final["error_code"] == "ATTACHMENT_ERROR"


def test_optional_attachment_failure_does_not_fail_job(client, prompt) -> None:
    upload = client.post(
        "/api/uploads",
        files=[
            ("files", ("good.txt", b"usable content", "text/plain")),
            ("files", ("scan.pdf", build_scanned_like_pdf(2), "application/pdf")),
        ],
    ).json()
    required = {
        f["attachment_id"]: f["original_filename"] == "good.txt" for f in upload["files"]
    }
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1. 테스트 청구항",
            "batch_id": upload["batch_id"],
            "required_map": required,
        },
    ).json()
    final = wait_for_job(client, job["id"])
    assert final["status"] == "SUCCEEDED"
    assert final["error_code"] is None


def test_attachment_content_reaches_final_prompt(client, prompt) -> None:
    upload = client.post(
        "/api/uploads",
        files=[("files", ("evidence.txt", "고유표식XYZ123".encode(), "text/plain"))],
    ).json()
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1. 테스트 청구항",
            "batch_id": upload["batch_id"],
        },
    ).json()
    wait_for_job(client, job["id"])
    text = client.get(f"/api/jobs/{job['id']}/final-prompt").text
    assert "고유표식XYZ123" in text
    assert "SYSTEM PROMPT" in text


def test_input_too_large(client, prompt) -> None:
    """사용자가 직접 건 글자 수 한도는 그대로 지킨다.

    기본값은 제한 없음(0)이지만, 값을 넣어 두면 그 한도에서 막는다. 자르거나
    요약하지 않고 INPUT_TOO_LARGE 로 끝낸다.
    """
    client.put("/api/settings", json={"values": {"max_inline_chars": 1500}})
    try:
        upload = client.post(
            "/api/uploads", files=[("files", ("big.txt", b"A" * 4000, "text/plain"))]
        ).json()
        job = client.post(
            "/api/jobs",
            json={

                "provider": "test",
                "claim_text": "청구항 1.",
                "batch_id": upload["batch_id"],
            },
        ).json()
        final = wait_for_job(client, job["id"])
        assert final["status"] == "FAILED"
        assert final["error_code"] == "INPUT_TOO_LARGE"
    finally:
        # 기본값으로 되돌린다. 0 = 제한 없음.
        client.put("/api/settings", json={"values": {"max_inline_chars": 0}})


def test_inline_char_limit_defaults_to_unlimited(client, prompt) -> None:
    """PRISM 자체 글자 수 한도는 기본적으로 없다.

    0 과 null 을 모두 '제한 없음'으로 받고, 그 상태에서는 큰 입력도 글자 수를
    이유로 막지 않는다. 실행을 막아야 하는 한도는 Provider 전송 한도와 모델
    컨텍스트 한도뿐이며, 그 둘은 이 설정과 무관하게 남는다
    (test_provider_byte_budget_blocks_oversized_input 참조).
    """
    assert client.get("/api/settings").json()["values"]["max_inline_chars"] == 0

    # null 도 같은 뜻으로 받는다.
    values = client.put(
        "/api/settings", json={"values": {"max_inline_chars": None}}
    ).json()["values"]
    assert values["max_inline_chars"] == 0

    upload = client.post(
        "/api/uploads",
        files=[("files", ("big.txt", "가".encode("utf-8") * 300_000, "text/plain"))],
    ).json()
    # 업로드 응답의 한도도 "제한 없음"을 그대로 전한다.
    assert upload["max_inline_chars"] is None

    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1.",
            "batch_id": upload["batch_id"],
        },
    ).json()
    final = wait_for_job(client, job["id"])
    assert final["status"] == "SUCCEEDED", final.get("error_message")


def test_provider_byte_budget_blocks_oversized_input(client, prompt, monkeypatch) -> None:
    """문자수 한도는 통과해도 Provider 의 바이트 한도를 넘으면 실행 전에 막는다.

    agy 처럼 큰 입력을 조용히 자르는 Provider 를 위한 방어다. 자르기 전에 실패로
    끝내, 앞부분만 분석하고 '성공'으로 남는 낭비를 없앤다. PRISM 의 글자 수
    한도를 꺼 두어도(기본값) 이 검사는 남는다는 것을 고정한다 — 사용자 입력
    제한이 아니라 Provider 가 자료 전체를 손실 없이 전달할 수 있는 한도다.
    """
    from .fake_provider import DeterministicTestProvider

    # 테스트 대역에 작은 바이트 예산을 심는다. 기본 프롬프트는 이보다 작아
    # 통과하고, 아래의 거대한 청구항만 이를 넘긴다.
    monkeypatch.setattr(DeterministicTestProvider, "max_input_bytes", 100_000)

    # 글자 수 한도는 기본이 '제한 없음'이라 아무리 길어도 문자 검사는 통과한다.
    # 그러나 한글은 UTF-8 로 3 bytes 라서 ~600 KB, 100 KB 예산을 크게 넘는다.
    huge_claim = "청구항 1. " + "가" * 200_000

    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": huge_claim,
            "batch_id": citation_batch(client),
        },
    ).json()
    final = wait_for_job(client, job["id"])

    assert final["status"] == "FAILED"
    assert final["error_code"] == "INPUT_TOO_LARGE"


def test_job_rejects_custom_prompt(client):
    response = client.post('/api/jobs', json={'prompt_id': 'custom.md', 'provider': 'test'})
    assert response.status_code == 422
    assert '프롬프트 교체는 지원하지 않습니다' in response.text


def test_analysis_without_documents_is_rejected(client, prompt) -> None:
    """대비할 문헌이 없는 구성대비 분석은 실행을 만들지 않는다.

    문헌 없이 시작하면 모델이 없는 자료를 찾으러 파일 도구를 부르고, 도구를 끌
    수단이 없는 Provider 에서는 그 호출 하나로 실행이 죽는다. 살아남아도 내용은
    "인용발명 문헌 미제공" 뿐이다. 어느 쪽이든 사용량만 쓴다.
    """
    before_runs = set(PATHS.runs_dir.iterdir())
    before_history = len(client.get("/api/history").json())

    response = client.post(
        "/api/jobs",
        json={"provider": "test", "claim_text": "청구항 1."},
    )
    assert response.status_code == 400
    assert "인용발명 문헌" in response.json()["detail"]

    # 거절된 요청은 이력에도 실행 폴더에도 흔적을 남기지 않는다.
    assert len(client.get("/api/history").json()) == before_history
    assert set(PATHS.runs_dir.iterdir()) == before_runs


def test_analysis_without_claims_is_rejected(client, prompt) -> None:
    """청구항이 빈 구성대비 분석은 실행을 만들지 않는다.

    [출원발명 청구항]이 이번 실행의 분석 대상이다. 비어 있으면 대비할 기준이
    없어 사용량만 쓰고 끝난다. 문헌 첨부가 있어도 청구항이 없으면 거절한다.
    """
    before_history = len(client.get("/api/history").json())
    # 거절은 batch 를 소비하지 않으므로(청구항 검사가 앞선다) 하나를 재사용한다.
    batch = citation_batch(client)

    for empty in ("", "   ", "\n"):
        response = client.post(
            "/api/jobs",
            json={

                "provider": "test",
                "claim_text": empty,
                "batch_id": batch,
            },
        )
        assert response.status_code == 400, response.text
        assert "청구항" in response.json()["detail"]

    # 아예 청구항 필드를 생략해도 마찬가지다.
    omitted = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "batch_id": batch,
        },
    )
    assert omitted.status_code == 400
    assert "청구항" in omitted.json()["detail"]

    # 거절된 요청은 이력에 흔적을 남기지 않는다.
    assert len(client.get("/api/history").json()) == before_history


def test_batch_cannot_be_reused(client, prompt) -> None:
    upload = client.post(
        "/api/uploads", files=[("files", ("a.txt", b"content", "text/plain"))]
    ).json()
    body = {

        "provider": "test",
        "claim_text": "청구항 1. 테스트 청구항",
        "batch_id": upload["batch_id"],
    }
    first = client.post("/api/jobs", json=body)
    assert first.status_code == 201
    wait_for_job(client, first.json()["id"])
    assert client.post("/api/jobs", json=body).status_code == 400


def test_result_download_endpoint_is_removed(client, prompt) -> None:
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1. 테스트 청구항",
            "batch_id": citation_batch(client),
        },
    ).json()
    wait_for_job(client, job["id"])

    assert client.get(f"/api/jobs/{job['id']}/result?fmt=md").status_code == 404
    assert client.get(f"/api/jobs/{job['id']}/result?fmt=json").status_code == 404


def test_cancel_finished_job_is_noop(client, prompt) -> None:
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1. 테스트 청구항",
            "batch_id": citation_batch(client),
        },
    ).json()
    wait_for_job(client, job["id"])
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["cancelled"] is False


# ---------------------------------------------------------------- history


def test_history_lists_and_deletes(client, prompt) -> None:
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1. 테스트 청구항",
            "batch_id": citation_batch(client),
        },
    ).json()
    wait_for_job(client, job["id"])

    items = client.get("/api/history").json()
    assert any(i["id"] == job["id"] for i in items)

    filtered = client.get("/api/history?provider=test").json()
    assert all(i["provider"] == "test" for i in filtered)

    assert client.delete(f"/api/history/{job['id']}").status_code == 204
    assert client.get(f"/api/history/{job['id']}").status_code == 404


def test_history_delete_all_clears_database_and_stored_files(client, prompt) -> None:
    # 작업에 붙지 않은 업로드. 일괄 삭제가 이런 고아 행까지 지우는지 본다.
    upload = client.post(
        "/api/uploads", files=[("files", ("unused.txt", b"unused", "text/plain"))]
    ).json()
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1. 테스트 청구항",
            "batch_id": citation_batch(client),
        },
    ).json()
    wait_for_job(client, job["id"])

    orphan_dir = PATHS.runs_dir / "orphan-record"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "leftover.log").write_text("leftover", encoding="utf-8")
    legacy_artifact = PATHS.artifacts_dir / "legacy-result.txt"
    legacy_artifact.write_text("legacy", encoding="utf-8")

    response = client.delete("/api/history")
    assert response.status_code == 200
    assert response.json()["deleted"] >= 1
    assert client.get("/api/history").json() == []
    assert client.get(f"/api/history/{job['id']}").status_code == 404
    assert list(PATHS.runs_dir.iterdir()) == []
    assert list(PATHS.artifacts_dir.iterdir()) == []

    from app.db import session_scope
    from app.models import Attachment

    with session_scope() as session:
        assert session.query(Attachment).filter(
            Attachment.upload_batch == upload["batch_id"]
        ).count() == 0


# --------------------------------------------------------------- settings


def test_settings_roundtrip(client) -> None:
    original = client.get("/api/settings").json()
    assert "runtime_context" in original["values"]
    assert original["env_filtering"]["blocked_prefixes"]

    updated = client.put(
        "/api/settings", json={"values": {"default_timeout_seconds": 123}}
    ).json()
    assert updated["values"]["default_timeout_seconds"] == 123
    client.put("/api/settings", json={"values": {"default_timeout_seconds": 900}})


def test_settings_reject_unknown_key(client) -> None:
    response = client.put("/api/settings", json={"values": {"secret_api_key": "abc"}})
    assert response.status_code == 400


def test_settings_reject_out_of_range(client) -> None:
    assert (
        client.put(
            "/api/settings", json={"values": {"max_concurrency_per_provider": 999}}
        ).status_code
        == 400
    )


def test_concurrency_warning_surfaces(client) -> None:
    try:
        data = client.put(
            "/api/settings", json={"values": {"max_concurrency_per_provider": 3}}
        ).json()
        assert any("동시 실행" in w for w in data["warnings"])
    finally:
        client.put("/api/settings", json={"values": {"max_concurrency_per_provider": 1}})


def test_runtime_context_disable_warns(client) -> None:
    try:
        data = client.put(
            "/api/settings", json={"values": {"runtime_context_enabled": False}}
        ).json()
        assert any("첨부 문서" in w for w in data["warnings"])
    finally:
        client.put("/api/settings", json={"values": {"runtime_context_enabled": True}})


def test_runtime_context_reset(client) -> None:
    client.put("/api/settings", json={"values": {"runtime_context": "임시"}})
    restored = client.post("/api/settings/runtime-context/reset").json()
    assert "첨부 자료" in restored["values"]["runtime_context"]


def test_no_api_key_endpoint_exists(client) -> None:
    """API Key 를 받는 경로가 있어서는 안 된다."""
    from app.main import app

    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert not any("key" in p.lower() or "token" in p.lower() for p in paths)


# ------------------------------------------------------------------- 추론강도


def test_reasoning_effort_defaults_to_the_model_default(client) -> None:
    """기본값은 "모델 기본값" 이다. PRISM 이 레벨을 대신 정해 주지 않는다."""
    values = client.get("/api/settings").json()["values"]
    assert values["reasoning_effort"] == {}


def test_reasoning_effort_accepts_a_known_level(client) -> None:
    updated = client.put(
        "/api/settings", json={"values": {"reasoning_effort": {"codex": "high"}}}
    ).json()
    assert updated["values"]["reasoning_effort"] == {"codex": "high"}
    # 빈 값으로 되돌리면 키가 사라진다 = 모델 기본값.
    restored = client.put(
        "/api/settings", json={"values": {"reasoning_effort": {"codex": ""}}}
    ).json()
    assert restored["values"]["reasoning_effort"] == {}


def test_reasoning_effort_rejects_an_unknown_level(client) -> None:
    """오타 하나가 실행 전체를 실패로 만든다. 설정 화면에서 막는다."""
    assert (
        client.put(
            "/api/settings", json={"values": {"reasoning_effort": {"codex": "higher"}}}
        ).status_code
        == 400
    )


# ------------------------------------------------- 원문 이벤트 비노출 / 결과 저장

def _stream_events(client, job_id: str) -> list[dict]:
    """끝난 작업의 이벤트를 클라이언트가 받는 그대로 읽는다.

    BUS 내부를 들여다보지 않고 실제 SSE 경로를 쓴다. 작업이 끝나면 버스가
    닫히므로 재생 목록을 흘려보낸 뒤 스트림이 스스로 끝난다.
    """
    body = client.get(f"/api/jobs/{job_id}/events").text
    events = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[len("data: ") :])
        if payload.get("type") != "stream_end":
            events.append(payload)
    return events


def test_model_output_never_reaches_the_event_stream(client, prompt) -> None:
    """진행 신호만 나가고 모델 원문은 나가지 않는다.

    예전에는 result_stream 델타가 그대로 화면에 붙었다. 그 원문에는 기계 판독
    블록이 섞여 있어서, 완성되기 전까지 사용자가 보고서 자리에서 JSON 을 보고
    있어야 했다. 지금은 받은 글자 수만 내보낸다.
    """
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1. 이벤트 비노출 확인",
            "batch_id": citation_batch(client),
        },
    ).json()
    final = wait_for_job(client, job["id"])
    assert final["status"] == "SUCCEEDED"

    events = _stream_events(client, job["id"])
    assert events, "이벤트가 하나도 없습니다"

    types = {event["type"] for event in events}
    assert "result_stream" not in types
    assert "result_progress" in types

    # 어떤 이벤트에도 감사 블록이나 보고서 본문이 실려 있지 않아야 한다.
    blob = json.dumps(events, ensure_ascii=False)
    assert "PRISM_COMPONENT_ANALYSIS_V1" not in blob
    assert "PRISM_CITATION_MAPPING_V1" not in blob
    assert "citation_number" not in blob

    progress = [e["payload"] for e in events if e["type"] == "result_progress"]
    assert all(set(payload) == {"chars"} for payload in progress)
    # 글자 수는 늘어나기만 한다.
    counts = [payload["chars"] for payload in progress]
    assert counts == sorted(counts) and counts[-1] > 0


def test_completed_result_is_stored_and_served_clean(client, prompt) -> None:
    """완료된 결과는 블록을 걷어낸 형태로 저장되고 조회된다."""
    job = client.post(
        "/api/jobs",
        json={

            "provider": "test",
            "claim_text": "청구항 1. 결과 저장 확인",
            "batch_id": citation_batch(client),
        },
    ).json()
    final = wait_for_job(client, job["id"])

    assert final["status"] == "SUCCEEDED"
    assert final["result_text"].strip()
    assert "PRISM_COMPONENT_ANALYSIS_V1" not in final["result_text"]
    assert "PRISM_CITATION_MAPPING_V1" not in final["result_text"]

    # 파싱 결과는 따로 남는다 — 본문에서 지웠다고 잃어버리지 않는다.
    assert final["analysis_manifest"]["items"]

    # 완전성 점검은 저장하지 않고 조회 시점에 계산한다.
    completeness = final["analysis_completeness"]
    assert completeness is not None
    assert completeness["process_succeeded"] is True
    assert completeness["manifest_parsed"] is True
    assert completeness["reported_components"] == len(
        final["analysis_manifest"]["items"]
    )

    # 다시 조회해도 같은 값이 나온다(파생값이므로 저장 여부와 무관하다).
    again = client.get(f"/api/jobs/{job['id']}").json()
    assert again["analysis_completeness"] == completeness
