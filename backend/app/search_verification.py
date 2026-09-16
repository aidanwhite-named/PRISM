"""Mechanical evidence checks only. Never choose, rank, or reclassify candidates."""
from __future__ import annotations

import copy
import re
from . import search_manifest as manifest
from .config import PATHS
from .search_passages import passages
from .patent_search import gpatents_parser, literature_parser, oa_pdf
from .patent_search.artifacts import ArtifactStore
from .patent_search.base import EvidenceRef, FieldValue
from .patent_search.provenance import MATCH_EXACT, verify_excerpt

# 비공식 원문 페이지/PDF 조회 도구. 여기서 온 근거는 공식 등급으로 세지 않는다.
PAGE_TOOLS = frozenset({"gpatents_fetch", "literature_fetch_pdf"})
PAGE_TOOL = "gpatents_fetch"

ISSUE_LABELS = {
    "identifier_unverified": "식별 미확인",
    "identifier_invalid": "식별자 형식 오류",
    "identifier_mismatch": "응답 또는 URL의 문헌 식별자 불일치",
    "source_not_read": "페이지 본문 열람 미확인",
    "quote_unverified": "직접 인용 검증 불가",
    "passage_unverified": "선택한 발췌 구간 대조 미확인",
    "support_unverified": "근거 문장 대조 미확인",
    "duplicate_group_conflict": "동일 문헌에 대한 LLM 그룹 충돌",
    "publication_date_conflict": "공개일 출처 충돌",
    "publication_date_unverified": "공개일 대조 미확인",
    "source_conflict": "출처 간 필드 내용 차이",
    "title_unverified": "명칭 대조 미확인",
    "title_mismatch": "보고 명칭과 보존 원문 명칭 차이",
    "applicant_unverified": "저자·출원인 대조 미확인",
    "applicant_mismatch": "보고 저자·출원인과 보존 원문 차이",
}
LEVEL_LABELS = {
    "search_snippet_only": "검색 스니펫·모델 판단 / 원문 미검증",
    "source_page_reviewed": "페이지 열람 확인 / 원문 인용 미검증",
    "official_bibliographic": "공식 서지 확보",
    "official_abstract": "공식 초록 확보",
    # Google Patents 페이지를 PRISM 이 보존하고 발췌를 글자 그대로 대조한 경우.
    # 특허청 문서가 아니므로 공식 청구항보다 아래, 공식 서지·초록보다는 위다.
    "source_page_text_verified": "원문 페이지 대조 확인 (비공식 출처)",
    "official_claims": "공식 청구항 확보",
    "official_full_text": "공식 전문 확보",
}
SCOPES = ("bibliographic", "abstract", "claims", "description", "family", "page_text")

def _key(candidate: dict) -> str:
    return manifest.identity_key(candidate.get("doc_number", ""), candidate.get("doi", ""))

def _record_key(record: dict) -> str:
    number = str(record.get("document_number") or "")
    return manifest.identity_key(doi=number) if number.lower().startswith("10.") else manifest.identity_key(number)

def _ref_key(ref: dict) -> tuple:
    return tuple(str(ref.get(key) or "") for key in ("artifact_id", "field_path", "profile_id"))


def _text_position(row: dict, text: str, excerpt: str) -> int:
    selection = row.get("passage_resolution") or {}
    start, end = selection.get("start"), selection.get("end")
    if isinstance(start, int) and isinstance(end, int) and text[start:end] == excerpt:
        return start
    return text.find(excerpt)

def _scope(field: str) -> str:
    prefix = field.split(":")[0]
    if prefix in ("abstract", "claims", "description", "family", "full_text"):
        return prefix
    return "bibliographic"


def _party_names(text: str) -> set[str]:
    # Country tags and separators differ between OPS and model output.
    text = re.sub(r"\[[A-Z]{2}\]", "", text)
    return {normalized for part in re.split(r"[;\n]+", text)
            if (normalized := re.sub(r"[\W_]+", "", part.casefold()))}

def _matching_sources(candidate: dict, journal: list[dict], store) -> list[dict]:
    found = []
    for call in journal:
        if call.get("state") != "completed" or call.get("ok") is not True:
            continue
        result = call.get("result") or {}
        for record_index, record in enumerate(result.get("records", [])):
            if _record_key(record) != _key(candidate):
                continue
            refs = record.get("evidence_refs") or {}
            verified_fields = {}
            for name, text in (record.get("fields") or {}).items():
                ref = refs.get(name)
                if not isinstance(text, str) or not text or not isinstance(ref, dict):
                    continue
                evidence = EvidenceRef(*_ref_key(ref))
                check = verify_excerpt(excerpt=text, field=FieldValue(text, evidence), store=store)
                if check.verified:
                    verified_fields[name] = {"text": text, "evidence_ref": ref}
            if verified_fields:
                selectors = passages(str(record.get("document_number") or ""),
                                     {k: v["text"] for k, v in verified_fields.items()}, refs)
                # Accept only selectors actually delivered, with untouched bounds.
                selectors = [p for p in selectors if p in (record.get("evidence_passages") or [])]
                found.append({"tool": call.get("tool"), "call_id": call.get("id"),
                              "record_index": record_index,
                              "url": record.get("url", ""), "document_number": record.get("document_number"),
                              "fields": verified_fields, "passages": selectors})
    return found

def verify(reported: dict, observed: dict, journal: list[dict], *, store=None) -> dict:
    # MCP fetches run in another process. Verification must not depend on a
    # LiteratureBackend having happened to be constructed in this process.
    literature_parser.register()  # Includes OpenAlex, Crossref, Europe PMC and arXiv.
    result = copy.deepcopy(reported)
    store = store or ArtifactStore(PATHS.evidence_dir.resolve())
    read_urls = {manifest.normalize_url(url) for url in observed.get("succeeded_fetch_urls", [])}
    candidates, seen = [], {}
    for candidate in result["candidates"]:
        candidate["verification_issues"] = []
        key = _key(candidate)
        if key not in ("patent:", "doi:") and key in seen:
            first = seen[key]
            if first["group"] != candidate["group"]:
                first["verification_issues"].append("duplicate_group_conflict")
            continue
        seen[key] = candidate
        candidates.append(candidate)
    result["candidates"] = candidates
    for candidate in candidates:
        issues = candidate["verification_issues"]
        number, doi = candidate.get("doc_number", ""), candidate.get("doi", "")
        if doi:
            if not re.fullmatch(r"10\.\d{4,9}/\S+", manifest.identity_key(doi=doi)[4:], re.I):
                issues.append("identifier_invalid")
        elif number and not re.fullmatch(r"[A-Z]{2}\d+[A-Z]?\d?", manifest.identity_key(number)[7:]):
            issues.append("identifier_invalid")
        sources = _matching_sources(candidate, journal, store)
        candidate["evidence_sources"] = sources
        scope = {name: "not_requested" for name in SCOPES}
        delivered = {}
        delivered_passages = {}
        response_paths = {}
        values_by_field = {}
        # 출처 간 차이 판정은 공식 응답끼리만 한다. 페이지의 원어 제목·표기와 OPS 의
        # 영문 표기가 다른 것은 충돌이 아니다.
        official_values = {}
        official = False
        for source in sources:
            for passage in source.get("passages", []):
                field = source["fields"].get(passage["field"])
                if field:
                    delivered_passages[passage["passage_id"]] = (passage, field)
            page = source.get("tool") in PAGE_TOOLS
            official = official or not page
            for name, field in source["fields"].items():
                is_text = (
                    (source.get("tool") == "gpatents_fetch" and name in gpatents_parser.PAGE_TEXT_FIELDS)
                    or (source.get("tool") == "literature_fetch_pdf" and name in oa_pdf.PDF_TEXT_FIELDS)
                )
                kind = "page_text" if is_text else _scope(name)
                # 비공식 페이지/PDF의 서지는 식별 대조에만 쓰고 공식 확보 범위로 세지 않는다.
                if kind in scope and (not page or kind == "page_text"):
                    scope[kind] = "verified"
                delivered[_ref_key(field["evidence_ref"])] = field
                # A model may copy the response's JSON path instead of its
                # evidence_ref path. Bind only an actual delivered record/field,
                # for this publication and this exact artifact/profile.
                aid, _, profile = _ref_key(field["evidence_ref"])
                if aid and profile and isinstance(source.get("record_index"), int):
                    alias = (aid, f"records/{source['record_index']}/fields/{name}", profile)
                    response_paths.setdefault(alias, {})[_ref_key(field["evidence_ref"])] = field
                values_by_field.setdefault(name, set()).add(field["text"])
                if not page:
                    official_values.setdefault(name, set()).add(field["text"])
        # Fetch attempts are independent from the returned content. Failed or
        # missing constituents must not become document-wide 'verified'.
        for call in journal:
            args = call.get("arguments") or {}
            requested = args.get("publication_number") or args.get("doi") or ""
            if not requested:
                continue
            request_key = manifest.identity_key(doi=requested) if args.get("doi") else manifest.identity_key(requested)
            if request_key != _key(candidate):
                continue
            if call.get("tool") in PAGE_TOOLS:
                requested_scope = None if args.get("constituent") == "citations" else "page_text"
            else:
                requested_scope = args.get("constituent", "abstract" if args.get("doi") else "claims")
                requested_scope = "bibliographic" if requested_scope == "biblio" else requested_scope
            if requested_scope in scope and scope[requested_scope] != "verified":
                scope[requested_scope] = "unavailable"
            response = call.get("result") or {}
            if response.get("identifier_matched") is False:
                issues.append("identifier_mismatch")
        level = "search_snippet_only"
        url = manifest.normalize_url(candidate.get("url"))
        # Reading a different publication is not evidence for this candidate.
        explicit = re.search(r"/patent/([A-Z]{2}[\d/.-]+[A-Z]\d?)", str(candidate.get("url") or ""), re.I)
        url_mismatch = bool(explicit and number and
                            manifest.identity_key(explicit.group(1)) != manifest.identity_key(number))
        if url_mismatch:
            issues.append("identifier_mismatch")
        if url and url in read_urls and not url_mismatch:
            level = "source_page_reviewed"
        elif url and not sources:
            issues.append("source_not_read")
        if sources:
            if official:
                level = "official_bibliographic"
                if scope["abstract"] == "verified":
                    level = "official_abstract"
                if scope["claims"] == "verified":
                    level = "official_claims"
                if "full_text" in values_by_field:
                    level = "official_full_text"
            elif level == "search_snippet_only":
                # 페이지는 PRISM 이 받았지만 본문 필드는 아직 대조되지 않았다(인용 목록만 등).
                level = "source_page_reviewed"
        else:
            issues.append("identifier_unverified")
        candidate["verification_scope"] = scope
        candidate["evidence_level"] = level
        candidate["reported_publication_date"] = candidate.get("publication_date", "")
        # 같은 날짜의 표기 차이(2024-04-30 / 20240430)는 충돌이 아니다. 숫자로 맞춘 뒤
        # 처음 받은 표기를 그대로 둔다.
        dates = {}
        for text in sorted(values_by_field.get("publication_date", set())):
            dates.setdefault(re.sub(r"\D", "", text), text)
        candidate["publication_date"] = next(iter(dates.values())) if len(dates) == 1 else ""
        if len(dates) > 1:
            issues.append("publication_date_conflict")
        elif not dates:
            issues.append("publication_date_unverified")
        if any(len(values) > 1 for field, values in official_values.items() if field != "publication_date"):
            issues.append("source_conflict")
        titles = {text for name, values in values_by_field.items()
                  if name.split(":")[0] == "title" for text in values}
        candidate["verified_titles"] = sorted(titles)
        normalize_title = lambda text: re.sub(r"[\W_]+", "", text.casefold())
        if not titles:
            issues.append("title_unverified")
        elif normalize_title(candidate.get("title", "")) not in {normalize_title(text) for text in titles}:
            # A translated or shortened title can differ without implying a different document.
            issues.append("title_mismatch")
        applicants = {text for name, values in values_by_field.items()
                      if name.split(":")[0] in ("applicants", "authors") for text in values}
        candidate["verified_applicants"] = sorted(applicants)
        if not candidate.get("applicant") and len(applicants) == 1:
            candidate["applicant"] = next(iter(applicants))
        if not applicants:
            issues.append("applicant_unverified")
        elif _party_names(candidate.get("applicant", "")) not in [_party_names(text) for text in applicants]:
            issues.append("applicant_mismatch")
        # Candidate-level excerpts without a field reference cannot be verified.
        if candidate.get("verbatim_excerpt"):
            issues.append("quote_unverified")
        candidate["verbatim_excerpt"] = ""
        candidate["source_location"] = ""
        page_excerpt_verified = False
        for row in candidate["mapping"]:
            # Always re-evaluate from the model reference; never trust previously
            # supplied verification or repair metadata.
            row.pop("evidence_ref_resolution", None)
            row.pop("passage_resolution", None)
            ref = row.get("evidence_ref")
            field = delivered.get(_ref_key(ref)) if isinstance(ref, dict) else None
            if field is None and isinstance(ref, dict):
                matches = response_paths.get(_ref_key(ref), {})
                if len(matches) == 1:
                    field = next(iter(matches.values()))
                    resolved = field["evidence_ref"]
                    row["evidence_ref_resolution"] = {
                        "reason": "delivered_response_path",
                        "reported": copy.deepcopy(ref), "resolved": copy.deepcopy(resolved),
                    }
                    # Keep evidence_ref as reported for audit. Only this local
                    # reference is used for the same exact excerpt verification.
                    ref = resolved
            passage_id = row.get("evidence_passage_id")
            if passage_id:
                selection = delivered_passages.get(passage_id)
                if selection and (ref is None or _ref_key(ref) == _ref_key(selection[1]["evidence_ref"])):
                    passage, field = selection
                    ref = field["evidence_ref"]
                    text = field["text"][passage["start"]:passage["end"]]
                    restored = []
                    for target in ("support_text", "verbatim_excerpt"):
                        if not row.get(target):
                            row[target] = text
                            restored.append(target)
                    row["passage_resolution"] = {
                        "passage_id": passage_id, "start": passage["start"], "end": passage["end"],
                        "evidence_ref": copy.deepcopy(ref), "restored_fields": restored,
                    }
                else:
                    field = None
                    issues.append("passage_unverified")
            is_gp_page = bool(
                field
                and ref.get("profile_id") == gpatents_parser.PROFILE_GOOGLE_PATENTS_PAGE
                and ref.get("field_path") in gpatents_parser.PAGE_TEXT_FIELDS
            )
            is_oa_pdf = bool(
                field
                and ref.get("profile_id") == oa_pdf.PROFILE_OA_PDF_TEXT
                and oa_pdf.is_text_path(ref.get("field_path"))
            )
            page_field = is_gp_page or is_oa_pdf
            support = row.get("support_text") or ""
            row["support_verified"] = False
            row["quote_verified"] = False
            row["page_quote_verified"] = False
            row["support_origin"] = (
                "oa_pdf" if is_oa_pdf
                else ("google_patents_page" if is_gp_page else ("preserved_response" if field else ""))
            )
            row["support_location"] = ""
            if field and support and support in field["text"]:
                check = verify_excerpt(
                    excerpt=support, field=FieldValue(field["text"], EvidenceRef(*_ref_key(ref))), store=store
                )
                row["support_verified"] = check.verified
                if page_field and check.match_kind == MATCH_EXACT:
                    page_excerpt_verified = True
                if check.verified and page_field:
                    if is_oa_pdf:
                        row["support_location"] = oa_pdf.location_of(
                            ref["field_path"], field["text"], _text_position(row, field["text"], support))
                    else:
                        row["support_location"] = gpatents_parser.location_of(
                            ref["field_path"], field["text"], _text_position(row, field["text"], support))
            if support and not row["support_verified"]:
                issues.append("support_unverified")
            excerpt = row.get("verbatim_excerpt") or ""
            if field and excerpt and excerpt in field["text"]:
                check = verify_excerpt(
                    excerpt=excerpt, field=FieldValue(field["text"], EvidenceRef(*_ref_key(ref))), store=store
                )
                row["quote_verified"] = check.original_verified
                # 공식 원문은 아니지만 보존한 페이지/PDF에서 글자 그대로 확인됐다.
                row["page_quote_verified"] = page_field and check.match_kind == MATCH_EXACT
            if row["page_quote_verified"]:
                page_excerpt_verified = True
                # 위치는 모델이 적은 값을 쓰지 않고 페이지의 번호 표시에서 계산한다.
                # translation 은 모델의 번역이다. 대조 대상이 아니라 표시에서 구분한다.
                if is_oa_pdf:
                    row["source_location"] = oa_pdf.location_of(
                        ref["field_path"], field["text"], _text_position(row, field["text"], excerpt))
                else:
                    row["source_location"] = gpatents_parser.location_of(
                        ref["field_path"], field["text"], _text_position(row, field["text"], excerpt))
            elif not row["quote_verified"]:
                if excerpt:
                    issues.append("quote_unverified")
                row["verbatim_excerpt"] = ""
                row["translation"] = ""
                row["source_location"] = ""
            # Technical degree/counterpart/similar/different are model judgments.
            # They are intentionally never changed by these checks.
        # Scope records preservation of a field, not validation of a model's
        # excerpt. Only promote after an actual mapping passage matched.
        if page_excerpt_verified and level not in ("official_claims", "official_full_text"):
            candidate["evidence_level"] = "source_page_text_verified"
        candidate["verification_issues"] = list(dict.fromkeys(issues))
    return result
