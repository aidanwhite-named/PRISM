"""Read-only search tools. No candidate selection, ranking or classification."""
from __future__ import annotations
import json
import copy
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from . import search_dates, search_channels, search_manifest, settings_service
from .config import PATHS
from .db import session_scope
from .patent_search import get_backend, epo_cql
from .patent_search.base import PatentSearchError, PatentSearchQuery
from .patent_search.epo_client import scrub, credential_tokens

SERVER_NAME = "prism-search"
PROTOCOL_VERSION = "2025-06-18"
LEDGER_NAME = "search_tool_calls.jsonl"

@contextmanager
def _quota_lock(filename="epo-mcp.lock"):
    """Serialize OPS calls across MCP processes before syncing persistent quota."""
    path = PATHS.data_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

class ToolLimitExceeded(RuntimeError):
    pass


def error_response(exc, name, arguments, secrets=()):
    """Keep the OPS fault, but explain which scope failed and the next options."""
    value = {"error_code": getattr(exc, "fault_code", "") or type(exc).__name__,
             "detail": scrub(str(exc), *secrets)[:500]}
    args = arguments if isinstance(arguments, dict) else {}
    if name == "gpatents_fetch":
        value["requested_identifier"] = args.get("publication_number", "")
        value["not_evidence_of_absence"] = True
    if name == "epo_fetch":
        value["requested_identifier"] = args.get("publication_number", "")
        value["requested_constituent"] = args.get("constituent", "claims")
        value["not_evidence_of_absence"] = True
        if value["error_code"] == "CLIENT.InvalidCountryCode":
            value["recovery"] = {
                "reason": "Requested constituent is unsupported for this country; not a missing publication.",
                "suggested_constituents": ["biblio", "abstract", "family"],
                "next_step": "Try an unattempted bibliographic/abstract scope. For claims use a source page or an identified family publication; keep identities separate.",
            }
        elif value["error_code"] == "SERVER.EntityNotFound":
            value["recovery"] = {
                "reason": "The requested identifier/scope was not found; number format or scope coverage may be responsible.",
                "next_step": "Verify the publication number against a source page or OPS number-service conversion. Try an unattempted biblio scope; retain the unresolved candidate.",
            }
    return value


def epo_search_advice(result, begin=1):
    """Expose page sampling, not an estimate of technical recall."""
    coverage = result["coverage"]
    returned, total = coverage["returned_records"], coverage.get("total_results")
    coverage["result_range"] = f"{begin}-{begin + returned - 1}" if returned else None
    coverage["more_results_available"] = begin + returned - 1 < total if total is not None else None
    coverage["next_begin"] = begin + returned if returned and coverage["more_results_available"] and begin + returned <= 2000 else None
    dates = sorted(str(r.get("publication_date")) for r in result["records"] if r.get("publication_date"))
    coverage["publication_date_range"] = {"earliest": dates[0], "latest": dates[-1]} if dates else None
    coverage["ordering"] = "provider_default; no relevance ordering requested"
    warnings = []
    if total and returned and total > returned * 10:
        warnings.append({"code": "broad_query_sample", "artifact_id": result.get("raw_artifact_id", ""),
                         "detail": "Only a small page was read. Inspect date bias; refine technical relations, combine observed IPC/CPC with terms, partition the date range, or read another page. Do not infer absence from this page."})
    result["search_warnings"] = warnings
    return result


class SearchTools:
    def __init__(self, *, values=None, work_dir=None, max_calls=None, cutoff=None):
        self.work_dir = Path(work_dir or os.environ["PRISM_SEARCH_WORK_DIR"])
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.work_dir / LEDGER_NAME
        self.max_calls = max(1, int(max_calls or os.environ.get("PRISM_SEARCH_MAX_TOOL_CALLS", 40)))
        self.calls = sum(row.get("state") == "started" for row in search_manifest.read_tool_journal(self.work_dir))
        self.cutoff = search_dates.normalize_cutoff(cutoff if cutoff is not None else os.environ.get("PRISM_SEARCH_CUTOFF", ""))
        if values is None:
            with session_scope() as session:
                values = settings_service.get_all(session)
        self.values = values
        self.backends = {}
        openalex_key = str(values.get("literature_openalex_api_key") or "").strip()
        self.secrets = (
            *credential_tokens(str(values.get("epo_consumer_key") or ""), str(values.get("epo_consumer_secret") or "")),
            *((openalex_key,) if openalex_key else ()),
        )

    def _record(self, row: dict):
        row = {**row, "timestamp": datetime.now(timezone.utc).isoformat()}
        serialized = scrub(json.dumps(row, ensure_ascii=False), *self.secrets)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(serialized + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def statuses(self):
        return search_channels.availability(self.values)

    def tool_definitions(self):
        statuses = self.statuses()
        return [_CAPABILITIES] + [
            tool for tool in (_EPO_SEARCH, _EPO_FETCH, _LITERATURE_SEARCH, _LITERATURE_FETCH, _KIWEE_SEARCH, _KIWEE_FETCH,
                              _GPATENTS_FETCH)
            if statuses[tool["name"].split("_")[0]]["status"] == "available"
        ]

    def call(self, name: str, arguments: dict) -> dict:
        row = {"id": str(uuid.uuid4()), "tool": name, "arguments": arguments, "sequence": self.calls}
        if self.calls >= self.max_calls:
            self._record({**row, "state": "rejected", "ok": False, "error_code": "tool_call_limit_exceeded"})
            raise ToolLimitExceeded("tool_call_limit_exceeded")
        self.calls += 1
        row["sequence"] = self.calls
        self._record({**row, "state": "started"})
        try:
            definition = next((tool for tool in self.tool_definitions() if tool["name"] == name), None)
            if definition is None:
                raise ValueError("tool_unavailable")
            _validate(arguments, definition["inputSchema"])
            if name.endswith("_fetch"):
                expected = {**arguments, "constituent": arguments.get("constituent", "abstract" if name == "literature_fetch" else "claims")}
                for previous in reversed(search_manifest.read_tool_journal(self.work_dir)):
                    old = previous.get("arguments") or {}
                    actual = {**old, "constituent": old.get("constituent", "abstract" if name == "literature_fetch" else "claims")}
                    result = previous.get("result") or {}
                    if (previous.get("tool") == name and previous.get("state") == "completed"
                            and previous.get("ok") is True and actual == expected
                            and result.get("records") and not result.get("failed_sources")
                            and result.get("identifier_matched") is not False):
                        result = copy.deepcopy(result)
                        result["reused_from_call_id"] = previous["id"]
                        self._record({**row, "state": "completed", "ok": True, "result": result})
                        return result
            if name == "search_capabilities":
                result = {"tools": self.statuses(), "publication_cutoff": self.cutoff or None,
                          "tool_calls_used": self.calls, "tool_calls_limit": self.max_calls}
            elif name.startswith("epo_"):
                with _quota_lock():
                    # Sync another process's committed quota before every call.
                    backend = self._backend("epo")
                    with session_scope() as session:
                        backend.use_ledger(settings_service.epo_ledger(session))
                    result = self._execute(name, arguments)
                    if settings_service.epo_persist_error():
                        raise PatentSearchError("quota_persistence_failed")
            else:
                result = self._execute(name, arguments)
        except Exception as exc:
            self._record({**row, "state": "completed", "ok": False,
                          **error_response(exc, name, arguments, self.secrets)})
            raise
        self._record({**row, "state": "completed", "ok": True, "result": result})
        return result

    def _execute(self, name, arguments):
        if name == "epo_search":
            return self._epo_search(arguments)
        if name == "gpatents_fetch":
            return self._gpatents_fetch(arguments)
        backend_id = name.split("_")[0]
        if name.endswith("_fetch"):
            return self._fetch(backend_id, arguments, "doi" if backend_id == "literature" else "publication_number")
        return self._plain_search(backend_id, arguments)

    def _backend(self, backend_id):
        if backend_id not in self.backends:
            if backend_id == "epo":
                with session_scope() as session:
                    backend = settings_service.epo_backend_for(session)
            else:
                backend = get_backend(self.values, backend_id)
            if backend is None or not backend.status().configured:
                raise PatentSearchError("backend_unavailable")
            self.backends[backend_id] = backend
        return self.backends[backend_id]

    def _epo_search(self, arguments):
        node = _query_node(arguments["query"])
        normalized = []
        # Do not silently hide unknown publication dates with a DB-side cutoff.
        cql = epo_cql.build(node, normalized=normalized)
        begin = arguments.get("begin", 1)
        paging = {"begin": begin} if begin != 1 else {}
        response = self._backend("epo").search_structured(node, max_results=arguments.get("max_results", 10), **paging)
        return epo_search_advice({**_response(response, scope="bibliographic_search"), "cql": cql,
                "normalized_classifications": normalized, "publication_cutoff": self.cutoff or None}, begin)

    def _plain_search(self, backend_id, arguments):
        query = arguments["query"]
        backend = self._backend(backend_id)
        source = arguments.get("source")
        if backend_id == "literature" and source == "arxiv":
            response = backend.search_arxiv(query, arguments.get("max_results", 5))
        elif backend_id == "literature":
            if arguments.get("cites_doi") and source not in (None, "openalex"):
                raise ValueError("cites_doi_requires_openalex")
            response = backend.search(
                PatentSearchQuery(query, arguments.get("max_results", 10)),
                sources=_LITERATURE_SOURCES.get(source),
                openalex_mode=arguments.get("openalex_mode", "search"),
                cites_doi=arguments.get("cites_doi", ""),
            )
        else:
            response = backend.search(PatentSearchQuery(query, arguments.get("max_results", 10)))
        return {**_response(response, scope="bibliographic_search"), "query": query,
                "publication_cutoff": self.cutoff or None}

    def _gpatents_fetch(self, arguments):
        """원문 페이지 조회. 같은 문헌은 받은 페이지를 다시 읽고, 새 페이지 수는 센다.

        상한은 **새로 받은 페이지**에만 건다. 이미 받은 문헌의 다른 구역(명세서·인용)을
        읽는 것은 요청을 만들지 않으므로 막을 이유가 없다. 404·429 같은 실패도 요청을
        보낸 것이므로 센다.
        """
        from .patent_search import gpatents_backend

        backend = self._backend("gpatents")
        number = arguments["publication_number"]
        constituent = arguments.get("constituent", "claims")
        key = search_manifest.identity_key(number)
        cached, fetched = "", 0
        for row in search_manifest.read_tool_journal(self.work_dir):
            if row.get("tool") != "gpatents_fetch" or row.get("state") != "completed":
                continue
            result = row.get("result") or {}
            if row.get("ok") is True:
                if result.get("network_fetch"):
                    fetched += 1
                if (not cached and result.get("raw_artifact_id")
                        and search_manifest.identity_key(result.get("requested_identifier", "")) == key):
                    cached = result["raw_artifact_id"]
            elif str(row.get("error_code") or "") in _GPATENTS_REQUEST_ERRORS:
                fetched += 1
        if not cached and fetched >= backend.max_fetches_per_run:
            raise gpatents_backend.GooglePatentsFetchLimit(
                f"gpatents_fetch_limit: 이 실행에서 새로 받을 수 있는 페이지 "
                f"{backend.max_fetches_per_run}건을 다 썼습니다. 이미 받은 문헌은 계속 조회할 수 있습니다."
            )
        response = backend.fetch_document(number, constituent, cached_artifact_id=cached)
        result = _response(response, scope=constituent)
        result["requested_identifier"] = number
        result["identifier_matched"] = any(
            search_manifest.identity_key(record["document_number"]) == key for record in result["records"]
        )
        result["network_fetch"] = not cached
        result["source_notice"] = (
            "Google Patents 원문 페이지(비공식 출처). 특허청 공식 문서가 아닙니다. "
            "[claim N]·[NNNN]은 청구항·문단 번호 표시입니다."
        )
        return result

    def _fetch(self, backend_id, arguments, identifier_key):
        identifier = arguments[identifier_key]
        constituent = arguments.get("constituent", "abstract" if backend_id == "literature" else "claims")
        response = self._backend(backend_id).fetch_document(identifier, constituent)
        result = _response(response, scope=constituent)
        def identity(number):
            return search_manifest.identity_key(doi=number) if backend_id == "literature" else search_manifest.identity_key(number)
        result["requested_identifier"] = identifier
        result["identifier_matched"] = any(identity(record["document_number"]) == identity(identifier) for record in result["records"])
        return result

def _validate(value, schema, depth=0):
    if depth > 12:
        raise ValueError("arguments_too_deep")
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError("expected_object")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ValueError("unknown_argument")
        if set(schema.get("required", [])) - set(value):
            raise ValueError("missing_argument")
        for key, item in value.items():
            if key in props:
                _validate(item, props[key], depth + 1)
    elif kind == "string":
        if not isinstance(value, str) or not schema.get("minLength", 1) <= len(value) <= schema.get("maxLength", 500):
            raise ValueError("invalid_string")
    elif kind == "integer":
        if type(value) is not int or not schema.get("minimum", 1) <= value <= schema.get("maximum", 20):
            raise ValueError("invalid_integer")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("invalid_enum")

def _query_node(raw: Any, depth=0):
    if depth > epo_cql.MAX_DEPTH or not isinstance(raw, dict):
        raise ValueError("invalid_cql_structure_or_depth")
    kind = raw.get("type", "term")
    if kind == "term":
        if set(raw) - {"type", "field", "value", "match"}:
            raise ValueError("unknown_cql_term_field")
        return epo_cql.Term(field=raw.get("field", ""), value=raw.get("value", ""),
                            match=raw.get("match", epo_cql.MATCH_ALL))
    if kind == "group":
        items = raw.get("items")
        if set(raw) - {"type", "op", "items"} or not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise ValueError("invalid_cql_group")
        return epo_cql.Group(op=raw.get("op", ""), items=tuple(_query_node(item, depth+1) for item in items))
    if kind == "date_range":
        if set(raw) - {"type", "field", "begin", "end"}:
            raise ValueError("unknown_cql_date_field")
        return epo_cql.DateRange(field=raw.get("field", "pd"),
                                 begin=raw.get("begin", ""), end=raw.get("end", ""))
    raise ValueError("unsupported_cql_type")

def _response(response, *, scope: str) -> dict:
    stats = list(response.source_stats)
    returned = len(response.records)
    # A multi-source literature count is returned rows, not an index-wide total.
    is_search = scope == "bibliographic_search"
    total = response.total_found if not stats else None
    if len(stats) == 1:
        total = stats[0].get("total_results")
    families = {r.fields["family_id"].value for r in response.records if "family_id" in r.fields}
    unique = len({r.doc_number for r in response.records})
    coverage = {
        "returned_records": returned,
        "unique_documents": unique,
        # 여러 출처가 같은 문헌을 돌려준 수. 오류가 아니라 교차 확인 신호다.
        "duplicate_records": returned - unique,
        # 기본 첫 페이지 범위. EPO는 epo_search_advice에서 실제 begin으로 바꾼다.
        # 출처가 여럿이면 범위는 source_stats 에 따로 있다.
        "result_range": f"1-{returned}" if is_search and returned and len(stats) <= 1 else None,
        "total_results": total,
        "more_results_available": (total > returned) if total is not None else None,
        "returned_fraction": round(returned / total, 6) if total and not stats else None,
        "status": "partial_failure" if response.failed_sources and returned else (
            "failed" if response.failed_sources else "results" if returned else "zero_results"),
        "source_stats": [
            {**stat, "result_range": f"1-{stat['returned_records']}" if stat.get("returned_records") else None}
            for stat in stats
        ],
        "unique_families_in_page": len(families) if families else None,
        "abstracts_in_page": sum(any(k.startswith("abstract") for k in r.fields) for r in response.records),
        "classification_records_in_page": sum(any(k in r.fields for k in ("ipc", "cpc")) for r in response.records),
        "meaning": "Page/response coverage only; not technical relevance, recall, or claim coverage.",
    }
    return {
        "untrusted_external_data": True,
        "verification_scope": scope,
        "total_found": response.total_found,
        "raw_artifact_id": response.raw_artifact_id,
        "fetched_at": response.fetched_at,
        "http_status": response.http_status,
        "request_url": response.request_url,
        "notes": list(response.notes),
        "failed_sources": list(response.failed_sources),
        "records": [_record(record, compact=scope == "bibliographic_search") for record in response.records],
        "coverage": coverage,
        "available_fields": sorted({k.split(":")[0] for r in response.records for k in r.fields}),
        "scope_note": "Bibliographic search/abstract is not claims or full-text verification." if is_search else "Only returned fields were obtained.",
    }


def _record(record, *, compact=False) -> dict:
    fields = {}
    evidence = {}
    selected = set(record.fields)
    if compact:
        selected = {name for name in selected if name in {"applicants", "authors", "publication_date", "ipc", "cpc", "container"}}
        abstracts = [name for name in record.fields if name.split(":")[0] == "abstract"]
        if abstracts:
            selected.add("abstract:en" if "abstract:en" in abstracts else abstracts[0])
    limit = 1200 if compact else 40000
    for name, field in record.fields.items():
        if name not in selected:
            continue
        fields[name] = field.value[:limit]
        if field.evidence is not None:
            evidence[name] = {
                "artifact_id": field.evidence.artifact_id,
                "field_path": field.evidence.field_path,
                "profile_id": field.evidence.profile_id,
            }
    publication_date = fields.get("publication_date", "")
    return {
        "document_number": record.doc_number,
        "title": record.title,
        "url": record.source_url,
        "publication_date": publication_date,
        "fields": fields,
        "evidence_refs": evidence,
        "truncated_fields": [name for name, field in record.fields.items() if name in selected and len(field.value) > limit],
        "omitted_fields": [name for name in record.fields if name not in selected],
    }


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": True},
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


_QUERY_SCHEMA = {
    "type": "object",
    "description": 'Term: {type:"term",field:"ta",value:"image matching",match:"all"}. Group: {type:"group",op:"and"|"or"|"not",items:[nodes]}. Term fields: ti,ab,ta,txt,pa,in,pn,ap,pr,ct,ipc,cpc,cl. ct finds documents that cite a publication number (forward citations), e.g. {type:"term",field:"ct",value:"JP2009070340"}. Match: all/any/exact. Publication-date node: {type:"date_range",field:"pd",begin:"19000101",end:"20240131"}. A date-limited query may omit unknown dates; choose whether an additional unrestricted query is needed. Maximum nesting: 3.',
    "additionalProperties": True,
}
_EPO_SEARCH = _tool(
    "epo_search",
    "Search EPO OPS with structured CQL. Provider-default order is not relevance ranking. Inspect coverage/date range and broad_query_sample warnings. Use observed ipc/cpc or applicant (pa) plus technical terms, date_range partitions, or begin for subsequent pages. From a strong candidate, field ct lists later documents citing it. Keep each OR branch technically specific; match=any splits words with OR. Returns actual CQL and artifact references.",
    {"query": _QUERY_SCHEMA, "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
     "begin": {"type": "integer", "minimum": 1, "maximum": 2000}},
    ["query"],
)
_EPO_FETCH = _tool(
    "epo_fetch",
    "Fetch an EPO publication constituent: abstract, claims, description, biblio or family. biblio includes references_cited (backward citations with cited-by/phase/category) when OPS has them; they are often absent for JP. US claims/description are not supplied by OPS; use biblio/abstract and a source page or identified family for full text. Scope failure is not publication absence. EntityNotFound may require number-format verification; never silently drop a fetched candidate.",
    {"publication_number": {"type": "string"}, "constituent": {"type": "string", "enum": ["abstract", "claims", "description", "biblio", "family"]}},
    ["publication_number"],
)
# literature_search 의 source 값 -> 백엔드 출처. 없으면(None) 쓸 수 있는 곳 전부다.
_LITERATURE_SOURCES = {
    "crossref_epmc": ("crossref", "europepmc"),
    "openalex": ("openalex",),
}

_LITERATURE_SEARCH = _tool(
    "literature_search",
    "Search literature. Omit source to query Crossref, Europe PMC and OpenAlex together. source=crossref_epmc searches only Crossref and Europe PMC; source=openalex searches only OpenAlex (broad coverage incl. IEEE/Elsevier abstracts and arXiv); source=arxiv searches arXiv preprints with arXiv query syntax. openalex_mode=search matches full-text index (broad, noisy); title_and_abstract restricts to titles/abstracts (narrow). cites_doi=<DOI> searches only works that cite that DOI (OpenAlex citation expansion; query narrows within them). HTTP 429 from OpenAlex means daily quota exhausted, not absence of literature. Metadata/abstract is not PDF full text.",
    {"query": {"type": "string", "minLength": 1, "maxLength": 500}, "max_results": {"type": "integer", "minimum": 1, "maximum": 20}, "source": {"type": "string", "enum": ["crossref_epmc", "openalex", "arxiv"]}, "openalex_mode": {"type": "string", "enum": ["search", "title_and_abstract"]}, "cites_doi": {"type": "string", "minLength": 1, "maxLength": 200}},
    ["query"],
)
_LITERATURE_FETCH = _tool(
    "literature_fetch",
    "Fetch bibliographic or abstract evidence for an exact DOI (Europe PMC, then OpenAlex, then Crossref for abstracts; a record without an abstract does not stop the search). arXiv IDs are accepted as 10.48550/arXiv.<id>, arXiv:<id> or arxiv.org/abs/<id> (version suffix ignored for identity) and try arXiv first, then OpenAlex. Mismatched identities are rejected; PDF full text is not fetched.",
    {"doi": {"type": "string"}, "constituent": {"type": "string", "enum": ["abstract", "biblio"]}},
    ["doi"],
)
_KIWEE_SEARCH = _tool("kiwee_search", "Search the configured Kiwee patent backend.", {"query": {"type": "string"}, "max_results": {"type": "integer"}}, ["query"])
_KIWEE_FETCH = _tool("kiwee_fetch", "Fetch a document from the configured Kiwee backend.", {"publication_number": {"type": "string"}, "constituent": {"type": "string"}}, ["publication_number"])
_GPATENTS_FETCH = _tool(
    "gpatents_fetch",
    "Fetch and preserve the Google Patents page (UNOFFICIAL source, not a patent office document) for one publication number with kind code, e.g. JP2009070340A. PRISM builds the URL from the number (original-language page) and paces requests at human speed, so each new page takes several seconds and a run has a small page cap; fetch only promising candidates. Another constituent of an already fetched number reuses the preserved page. constituent: claims (default), description (up to 40000 chars), abstract, citations (patent citations and cited-by with * = examiner cited). Text is the original-language text; [claim N] and [NNNN] mark claim and paragraph numbers. Use exact continuous text from page_claims/page_description/page_abstract as support_text/verbatim_excerpt with the returned evidence_refs. 404 means the page is unavailable, not that the publication does not exist.",
    {"publication_number": {"type": "string", "minLength": 6, "maxLength": 40},
     "constituent": {"type": "string", "enum": ["claims", "description", "abstract", "citations"]}},
    ["publication_number"],
)
# 요청을 실제로 보낸 뒤 실패한 경우. 실행당 새 페이지 상한에 센다.
_GPATENTS_REQUEST_ERRORS = frozenset({"GooglePatentsNotFound", "GooglePatentsRateLimited", "GooglePatentsHttpError"})
_CAPABILITIES = _tool("search_capabilities", "Report which PRISM search tools are enabled and configured without making a network request.", {}, [])


def _reply(request_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": request_id}
    payload["error" if error is not None else "result"] = error if error is not None else result
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()

INACTIVE_ERROR = "not_in_prism_search_run"

def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    # agy 는 전역 설정의 MCP 서버를 모든 세션에서 띄운다. PRISM 검색 실행이 넘긴
    # 작업 폴더가 없으면(터미널의 agy, 문서 분석 실행) 도구를 내놓지 않는다.
    tools = SearchTools() if os.environ.get("PRISM_SEARCH_WORK_DIR") else None
    while True:
        line = sys.stdin.readline(65537)
        if not line:
            break
        request_id = None
        try:
            if len(line) > 65536:
                raise ValueError("request_too_large")
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                raise ValueError("invalid_request")
            request_id = request.get("id")
            method = request.get("method")
            if request_id is None:
                continue
            if method == "initialize":
                _reply(request_id, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                                    "serverInfo": {"name": SERVER_NAME, "version": "1"}})
            elif method == "ping":
                _reply(request_id, {})
            elif method == "tools/list":
                _reply(request_id, {"tools": tools.tool_definitions() if tools else []})
            elif method == "tools/call" and tools is None:
                value = {"error_code": INACTIVE_ERROR, "detail": "PRISM 검색 실행 밖에서는 도구를 제공하지 않습니다."}
                _reply(request_id, {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                                    "structuredContent": value, "isError": True})
            elif method == "tools/call":
                params = request.get("params") or {}
                try:
                    value = tools.call(str(params.get("name") or ""), params.get("arguments", {}))
                    error = False
                except Exception as exc:
                    value = error_response(exc, str(params.get("name") or ""), params.get("arguments", {}), tools.secrets)
                    error = True
                _reply(request_id, {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                                    "structuredContent": value, "isError": error})
            else:
                _reply(request_id, error={"code": -32601, "message": "Method not found"})
        except (ValueError, TypeError):
            _reply(request_id, error={"code": -32700, "message": "Invalid JSON-RPC request"})

if __name__ == "__main__":
    main()
