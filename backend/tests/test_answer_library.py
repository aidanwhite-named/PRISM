"""Report learning loop: import -> review -> approve -> bounded generation."""
import asyncio
import json
import sqlite3
import time

import pytest

from app import answer_extraction, answer_library as library
from app.answer_models import AnswerCase, AnswerExtraction, AnswerRevision
from app.db import session_scope, _backup_before_v2
from app.providers.base import ExecutionOutcome, NO_TOOLS
from .conftest import wait_for_job
from .fake_provider import DeterministicTestProvider
from .pdf_fixture import build_pdf, build_scanned_like_pdf

REPORT = "C1 sensor feedback: partial correspondence. The sensor does not control the motor."
SOURCE = "The sensor does not control the motor."


@pytest.fixture(autouse=True)
def isolate_library(client, tmp_path, monkeypatch):
    monkeypatch.setattr(library, "style_path", lambda: tmp_path / "report-style.md")
    with session_scope() as session:
        session.query(AnswerCase).delete()
    yield
    with session_scope() as session:
        session.query(AnswerCase).delete()


def register(client, report=REPORT, pdf=False):
    data = build_pdf([report]) if pdf else report.encode()
    response = client.post("/api/answers", data={"title": "Sensor control", "claim_text": "C1 sensor feedback"},
        files=[("report", ("answer.pdf" if pdf else "answer.md", data)),
               ("sources", ("D1.txt", SOURCE.encode()))])
    assert response.status_code == 200, response.text
    return response.json()


def review(client, case, quote=REPORT):
    source_id = next(f["id"] for f in case["files"] if f["kind"] == "source")
    draft = {"style_rules": ["Use short declarative sentences."], "examples": [{
        "element": "C1 sensor feedback", "issue": "control relationship", "judgment": "partial",
        "reason": "No control relationship", "report_quote": quote, "source_id": source_id,
        "source_quote": SOURCE, "location": "paragraph 1"}]}
    response = client.put(f"/api/answers/{case['id']}", json={
        "title": case["title"], "claim_text": case["claim_text"], "report_text": case["report_text"],
        "draft": draft, "edit_version": case["edit_version"]})
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, case):
    response = client.post(f"/api/answers/{case['id']}/approve", json={"edit_version": case["edit_version"]})
    assert response.status_code == 200, response.text
    return response.json()


def context(claim="sensor feedback control motor", **kwargs):
    with session_scope() as session:
        return library.build_context(session, claim, **kwargs)


def test_pdf_original_and_review_are_separate_and_only_approval_is_used(client):
    case = register(client, pdf=True)
    assert case["status"] == "draft" and "--- PAGE 1 ---" in case["original_report"]
    assert context()["examples"] == []
    original_file = next(f for f in case["files"] if f["kind"] == "report")
    assert client.get(f"/api/answers/{case['id']}/files/{original_file['id']}").content.startswith(b"%PDF")
    case = review(client, case)
    assert context()["examples"] == []
    case = approve(client, case)
    result = context()
    assert len(result["examples"]) == 1 and result["style"]
    assert result["examples"][0]["source_verified"]
    assert result["estimated_tokens"] <= result["token_budget"]
    # Approval is idempotent; unchanged correct outputs need no comment.
    assert approve(client, case)["revision"] == 1
    changed = review(client, case)
    assert context()["examples"] == []
    assert changed["original_report"] == case["original_report"]
    approve(client, changed)
    revisions = client.get(f"/api/answers/{case['id']}/revisions").json()
    assert [r["revision"] for r in revisions] == [2, 1]
    assert revisions[1]["snapshot"]["examples"][0]["report_quote"] == REPORT


def test_fabricated_report_quote_or_source_quote_cannot_be_approved(client):
    case = review(client, register(client))
    for field, replacement in (("report_quote", "Invented conclusion"), ("source_quote", "Invented evidence"), ("source_id", "missing")):
        draft = json.loads(json.dumps(case["draft"]))
        draft["examples"][0][field] = replacement
        response = client.put(f"/api/answers/{case['id']}", json={k: case[k] for k in ("title", "claim_text", "report_text", "edit_version")} | {"draft": draft})
        assert response.status_code == 422, response.text
    assert client.get(f"/api/answers/{case['id']}").json()["draft"] == case["draft"]


def test_scanned_pdf_and_invalid_uploads_are_visible_not_learning_data(client):
    response = client.post("/api/answers", data={"title": "Scanned"}, files={"report": ("scan.pdf", build_scanned_like_pdf())})
    assert response.status_code == 200
    case = response.json()
    assert case["extraction_error"] and case["status"] == "draft"
    assert client.post(f"/api/answers/{case['id']}/extract", json={"provider": "test"}).status_code == 422
    assert client.post(f"/api/answers/{case['id']}/approve", json={"edit_version": 0}).status_code == 422
    response = client.post("/api/answers", data={"title": "Invalid"}, files={"report": ("bad.pdf", b"not PDF")})
    assert response.status_code == 422


def test_style_file_budget_disabled_context_and_unrelated_cases(client):
    assert client.put("/api/answers/style", json={"text": "Use concise Korean."}).status_code == 200
    assert library.style_path().read_text(encoding="utf-8") == "Use concise Korean."
    case = approve(client, review(client, register(client)))
    assert context()["style"] == "Use concise Korean."
    assert context(enabled=False)["text"] == ""
    assert context("unrelated chemistry molecule")["examples"] == []
    # Cases grow, prompt budget and maximum count do not.
    for _ in range(9):
        approve(client, review(client, register(client)))
    result = context()
    assert len(result["examples"]) <= 5 and result["estimated_tokens"] <= 6000
    assert client.put("/api/answers/style", json={"text": "x" * 3001}).status_code == 422
    assert client.post(f"/api/answers/{case['id']}/archive", json={"edit_version": case["edit_version"]}).status_code == 200
    assert all(e["case_id"] != case["id"] for e in context()["examples"])


def test_style_reaches_the_model_once_and_examples_do_not_repeat_it(client):
    approve(client, review(client, register(client)))
    pooled = context()
    # Per-case rules are pooled into "style". Carrying them on every example
    # spends the token budget on the same text once per selected judgment.
    assert pooled["examples"] and all("style_rules" not in e for e in pooled["examples"])
    assert pooled["text"].count("Use short declarative sentences.") == 1
    assert client.put("/api/answers/style", json={"text": "Use concise Korean."}).status_code == 200
    overridden = context()
    # A shared file replaces the extracted rules; it must not compete with them.
    assert overridden["style"] == "Use concise Korean."
    assert "Use short declarative sentences." not in overridden["text"]


def test_same_case_replay_excluded_and_stale_review_rejected(client):
    case = approve(client, review(client, register(client)))
    response = client.put(f"/api/answers/{case['id']}", json={k: case[k] for k in ("title", "claim_text", "report_text", "draft")} | {"edit_version": -1})
    assert response.status_code == 409
    with session_scope() as session:
        row = session.get(AnswerCase, case["id"])
        hashes = {f["sha256"] for f in row.files if f["kind"] == "source"}
    assert context(case["claim_text"], exclude_source_hashes=hashes)["examples"] == []


def test_job_preflight_and_execution_use_identical_snapshot(client):
    case = approve(client, review(client, register(client)))
    prompt = client.post("/api/prompts", json={"name": "Report with answers", "body": "Compare the provided evidence."}).json()
    batch = client.post("/api/uploads", files=[("files", ("current.txt", b"A current motor controller and sensor"))]).json()
    payload = {"provider": "test", "prompt_id": prompt["id"], "claim_text": "C1 motor with sensor feedback control",
               "batch_id": batch["batch_id"], "use_answer_library": True}
    preflight = client.post("/api/jobs/preflight", json=payload)
    assert preflight.status_code == 200, preflight.text
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 201, response.text
    job = response.json()
    assert preflight.json()["report_context"] == job["report_context"]
    assert len(job["report_context"]["examples"]) == 1
    client.put("/api/answers/style", json={"text": "NEW STYLE THAT MUST NOT LEAK"})
    client.post(f"/api/answers/{case['id']}/archive", json={"edit_version": case["edit_version"]})
    wait_for_job(client, job["id"])
    final = client.get(f"/api/jobs/{job['id']}/final-prompt").text
    assert job["report_context"]["text"] in final
    assert "NEW STYLE THAT MUST NOT LEAK" not in final
    assert client.get(f"/api/jobs/{job['id']}").json()["report_context"] == job["report_context"]


def test_linked_inputs_survive_history_deletion(client):
    prompt = client.post("/api/prompts", json={"name": "Library source", "body": "Compare."}).json()
    batch = client.post("/api/uploads", files=[("files", ("source.txt", SOURCE.encode()))]).json()
    response = client.post("/api/jobs", json={"provider": "test", "prompt_id": prompt["id"], "claim_text": "sensor", "batch_id": batch["batch_id"]})
    job = wait_for_job(client, response.json()["id"])
    response = client.post("/api/answers", data={"title": "Keep source", "source_job_id": job["id"]},
        files={"report": ("answer.md", REPORT.encode())})
    assert response.status_code == 200, response.text
    case = response.json()
    source = next(f for f in case["files"] if f["kind"] == "source")
    url = f"/api/answers/{case['id']}/files/{source['id']}"
    assert client.delete(f"/api/history/{job['id']}").status_code == 204
    assert client.get(url).content == SOURCE.encode()
    assert client.get(f"/api/answers/{case['id']}").status_code == 200


class ExtractionProvider(DeterministicTestProvider):
    bad = False
    async def execute(self, request, emit):
        payload = json.loads(request.user_message)
        example = {"element": "sensor", "judgment": "partial", "report_quote": payload["report"]}
        if self.bad:
            example["report_quote"] = "unwritten invented quote"
        return ExecutionOutcome(result_text=json.dumps({"style_rules": ["Concise sentences."], "examples": [example]}),
            exit_code=0, terminal_reason="completed", tool_policy=NO_TOOLS)


def wait_extraction(client, case_id):
    for _ in range(100):
        row = client.get(f"/api/answers/{case_id}").json()
        if row["extractions"][0]["status"] not in ("queued", "running"):
            return row
        time.sleep(.02)
    pytest.fail("extraction did not finish")


def test_llm_extraction_is_audited_and_never_auto_approved(client, monkeypatch):
    provider = ExtractionProvider()
    monkeypatch.setattr(answer_extraction, "build_provider", lambda *args: provider)
    case = register(client)
    response = client.post(f"/api/answers/{case['id']}/extract", json={"provider": "test"})
    assert response.status_code == 200, response.text
    case = wait_extraction(client, case["id"])
    attempt = case["extractions"][0]
    assert attempt["status"] == "succeeded", attempt
    assert attempt["execution_manifest"]["verdict"]["status"] == "SUCCEEDED"
    assert case["draft"]["examples"] and case["status"] == "draft"
    assert context()["examples"] == []
    original = attempt["result_text"]
    provider.bad = True
    client.post(f"/api/answers/{case['id']}/extract", json={"provider": "test"})
    case = wait_extraction(client, case["id"])
    assert case["extractions"][0]["status"] == "failed"
    assert case["extractions"][1]["result_text"] == original
    assert case["draft"]["examples"][0]["report_quote"] == REPORT


def test_input_gate_fails_before_provider_call(client, monkeypatch):
    provider = ExtractionProvider()
    provider.max_input_bytes = 1
    monkeypatch.setattr(answer_extraction, "build_provider", lambda *args: provider)
    case = register(client)
    client.post(f"/api/answers/{case['id']}/extract", json={"provider": "test"})
    row = wait_extraction(client, case["id"])
    assert row["extractions"][0]["status"] == "failed"
    assert "입력 한도" in row["extractions"][0]["error"]
    assert row["extractions"][0]["result_text"] is None


def test_v1_backup_contains_original_rows_and_only_runs_before_v2(tmp_path):
    path = tmp_path / "prism.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE execution_jobs (id TEXT)")
        db.execute("INSERT INTO execution_jobs VALUES ('v1-job')")
    _backup_before_v2(path)
    backups = list((tmp_path / "backups").glob("*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT id FROM execution_jobs").fetchone()[0] == "v1-job"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE answer_cases (id TEXT)")
    _backup_before_v2(path)
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1
