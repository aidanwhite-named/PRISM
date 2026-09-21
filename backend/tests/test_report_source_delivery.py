"""Full-text routing and unbiased original evidence delivery regressions."""
import json
from dataclasses import replace

from app import retrieval
from app.enums import DeliveryPlan
from app.providers import model_limits
from app.retrieval import evidence
from app.retrieval.agent import ComponentState, RetrievalBudget, RetrievalRun
from .test_delivery_modes import _assemble
from .test_retrieval import KOREAN_PAGES, _corpus, _pdf_attachment


def test_catalog_uses_effective_default_and_explicit_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [{
        "slug": "gpt-5-test", "context_window": 272000,
        "max_context_window": 872000, "effective_context_window_percent": 95,
    }]}))
    args = dict(provider_id="codex", model="gpt-5-test", reserve_tokens=32000,
                fallback_context_tokens=128000)
    budget = model_limits.token_budget(**args)
    assert budget.source == "codex_catalog" and budget.input_tokens == 226400
    assert model_limits.token_budget(**args, overrides={"codex:gpt-5-test": 64000}).input_tokens == 32000
    assert model_limits.token_budget(**{**args, "model": "gpt-5-other"}).source == "fallback"
    assert model_limits.token_budget(**{**args, "provider_id": "claude"}).source == "fallback"
    (tmp_path / "models_cache.json").write_text("broken")
    assert model_limits.token_budget(**args).source == "fallback"


def test_uncached_tokenizer_falls_back_without_loading_tiktoken(tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    model_limits._cached_encoder.cache_clear()
    try:
        assert model_limits.estimate_tokens("가" * 1000, provider_id="codex", model="gpt-5-test") == 1500
        (tmp_path / model_limits.encoding_cache_path().name).write_bytes(b"corrupt")
        model_limits._cached_encoder.cache_clear()
        assert model_limits._cached_encoder() is None
    finally:
        model_limits._cached_encoder.cache_clear()


def test_actual_assembly_prefers_full_text_with_catalog_and_counts_tokens(tmp_path, monkeypatch):
    class Encoder:
        def encode(self, text, **kwargs):
            return [0] * (len(text) // 2)
    monkeypatch.setattr(model_limits, "_cached_encoder", lambda: Encoder())
    monkeypatch.setattr(model_limits, "_catalog_context", lambda model: 258400)
    assembly = _assemble(tmp_path, provider_id="codex", model="gpt-5-test", provider_byte_budget=None,
                         unknown_model_context_tokens=128000, model_output_reserve_tokens=32000)
    assert assembly.delivery_plan == DeliveryPlan.FULL_INLINE
    assert "제40 실시예" in assembly.representative.user_message
    record = assembly.delivery_manifest(None)
    assert record["model_token_budget"]["source"] == "codex_catalog"
    assert record["full_inline_tokens"] >= 16384


def test_unselected_hit_keeps_original_but_does_not_become_a_match(tmp_path):
    corpus, _ = _corpus(tmp_path, [_pdf_attachment(tmp_path, "doc.pdf", KOREAN_PAGES)])
    try:
        document = corpus[0]
        row = document.index.page_rows(4)[0]
        state = ComponentState("R001", "현재 구성", "현재 구성")
        state.hit_chunks[f"{document.attachment_id}:{row.chunk_id}"] = {
            "alias": document.alias, "chunk_id": row.chunk_id, "page_number": 4,
            "snippet": "MODEL INVENTED TEXT", "score": 1,
        }
        budget = RetrievalBudget()
        run = RetrievalRun(components=[state], exposed_chunks={(document.attachment_id, row.chunk_id)})
        builder = evidence.EvidenceBuilder(corpus=corpus, run=run, budget=budget,
                    claim_text="현재 구성", semantic={}, capabilities={"trigram": True}, library_versions={})
        bundle = builder.build()
        component = bundle["components"][0]
        component["ai_note"] = "BIASED CONCLUSION"
        assert not component["findings"] and component["status"] != evidence.STATUS_MATCHED
        rendered = evidence.fit(bundle, budget)
        assert row.text in rendered and "크로스체크지표" in rendered
        assert "MODEL INVENTED TEXT" not in rendered and "BIASED CONCLUSION" not in rendered
        assert component["ai_note"] == "BIASED CONCLUSION"
        # Delivery limits may drop supplementary candidates but record omissions.
        limited = replace(budget, max_evidence_chars=len(rendered) - len(row.text))
        fitted = evidence.fit(bundle, limited)
        assert bundle["candidate_sources_omitted"] == 1
        assert len(fitted) <= limited.max_evidence_chars or bundle.get("package_over_budget")
        # A guessed/unexposed chunk cannot enter the original source pool.
        run.exposed_chunks.clear()
        assert not builder.build()["candidate_sources"]
    finally:
        retrieval.close_documents(corpus)
