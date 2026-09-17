"""Read-only search tools. No candidate selection, ranking or classification."""
from __future__ import annotations
import json
import copy
import os
import sys
import uuid
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from . import search_dates, search_channels, search_manifest, settings_service, search_budget
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
    if name == "epo_fetch":
        value["requested_identifier"] = args.get("publication_number", "")
        value["requested_constituent"] = args.get("constituent", "claims")
        value["not_evidence_of_absence"] = True
        if value["error_code"] == "CLIENT.InvalidCountryCode":
            value["recovery"] = {
                "reason": "Requested constituent is unsupported for this country; not a missing publication.",
                "suggested_constituents": ["biblio", "abstract"],
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
        self._lock = threading.RLock()
        self._source_locks = {name: threading.RLock() for name in ('epo', 'literature', 'kipris', 'arxiv')}
        self.disabled_sources = {}
        openalex_key = str(values.get("literature_openalex_api_key") or "").strip()
        self.secrets = (
            str(values.get('kipris_api_key') or ''),
            *credential_tokens(str(values.get("epo_consumer_key") or ""), str(values.get("epo_consumer_secret") or "")),
            *((openalex_key,) if openalex_key else ()),
        )

    def _record(self, row: dict):
        row = {**row, "timestamp": datetime.now(timezone.utc).isoformat()}
        serialized = scrub(json.dumps(row, ensure_ascii=False), *self.secrets)
        with self._lock:
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(serialized + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def statuses(self):
        statuses = search_channels.availability(self.values)
        for source, detail in self.disabled_sources.items():
            statuses[source] = {**statuses.get(source, {}), 'status': 'unavailable', 'detail': detail}
        return statuses

    def budget(self):
        try:
            native = json.loads((self.work_dir / search_budget.NATIVE_COUNT_FILE).read_text(encoding="utf-8"))
            native = max(0, int(native))
        except (OSError, ValueError, TypeError):
            native = 0
        result = search_budget.budget_status(self.calls + native, self.max_calls)
        try:
            deadline = float((self.work_dir / 'search_deadline.json').read_text(encoding='utf-8'))
            result['seconds_remaining'] = max(0, round(deadline - time.time()))
            if result['seconds_remaining'] <= 30:
                result.update(action='finalize_now', instruction='시간 한도가 임박했습니다. 현재 후보를 저장하고 미확인 사항을 포함한 최종 JSON을 작성하십시오.')
        except (OSError, ValueError):
            pass
        return result

    def tool_definitions(self):
        statuses = self.statuses()
        return [_CAPABILITIES, _SAVE_CANDIDATES, _SOURCE_FETCH, _CITATION_SEARCH, _START_COLLECTION, _COLLECT_RESULTS] + [
            tool for tool in (_EPO_SEARCH, _EPO_FETCH, _LITERATURE_SEARCH, _LITERATURE_FETCH, _KIPRIS_SEARCH)
            if statuses[tool["name"].split("_")[0]]["status"] == "available"
        ]

    def call(self, name: str, arguments: dict) -> dict:
        if name in ('epo_search', 'kipris_search', 'literature_search') and isinstance(arguments, dict):
            size = arguments.get('max_results', 3)
            if type(size) is int and size > 0:
                arguments = {**arguments, 'max_results': min(size, 4)}
        with self._lock:
            return_row = self._reserve_call(name, arguments)
        return self._run_call(name, arguments, return_row)

    def _reserve_call(self, name, arguments):
        row = {"id": str(uuid.uuid4()), "tool": name, "arguments": arguments, "sequence": self.calls}
        if name not in ('save_candidates', 'collect_results') and self.budget()["remaining"] <= 0:
            self._record({**row, "state": "rejected", "ok": False, "error_code": "tool_call_limit_exceeded"})
            raise ToolLimitExceeded("tool_call_limit_exceeded")
        self.calls += 1
        row["sequence"] = self.calls
        self._record({**row, "state": "started"})
        return row

    def _run_call(self, name, arguments, row):
        try:
            definition = next((tool for tool in self.tool_definitions() if tool["name"] == name), None)
            if definition is None:
                raise ValueError("tool_unavailable")
            _validate(arguments, definition["inputSchema"])
            budget = self.budget()
            if name not in ("search_capabilities", "save_candidates", "collect_results") and (
                    budget['used'] > budget['finalize_at'] or budget.get('seconds_remaining', 999999) <= 10
                    or (self.work_dir / 'search_x_complete.json').exists()):
                result = {"records": [], "budget": self.budget(), "budget_stopped": True,
                          "not_evidence_of_absence": True}
                self._record({**row, "state": "completed", "ok": True, "result": result})
                return result
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
                        result["budget"] = self.budget()
                        result["reused_from_call_id"] = previous["id"]
                        self._record({**row, "state": "completed", "ok": True, "result": result})
                        return result
            if name == "search_capabilities":
                result = {"tools": self.statuses(), "publication_cutoff": self.cutoff or None,
                          "tool_calls_used": self.calls, "tool_calls_limit": self.max_calls}
            elif name.startswith("epo_"):
                with self._source_locks['epo'], _quota_lock():
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
            if name == 'kipris_search' and getattr(exc, 'fault_code', '') in ('KIPRIS.30', 'KIPRIS.31'):
                self.disabled_sources['kipris'] = str(exc)
            self._record({**row, "state": "completed", "ok": False,
                          **error_response(exc, name, arguments, self.secrets)})
            raise
        result["budget"] = self.budget()
        self._record({**row, "state": "completed", "ok": True, "result": result})
        return result

    def _execute(self, name, arguments):
        if name in ('start_collection', 'collect_results'):
            from . import search_session
            return getattr(search_session, name)(self, arguments)
        if name in ('save_candidates', 'source_fetch', 'citation_search'):
            from . import search_agent_tools
            return getattr(search_agent_tools, name)(self, arguments)
        if name == "epo_search":
            return self._epo_search(arguments)
        backend_id = name.split("_")[0]
        if name.endswith("_fetch"):
            with self._source_locks[backend_id]:
                return self._fetch(backend_id, arguments, "doi" if backend_id == "literature" else "publication_number")
        lock_id = 'arxiv' if backend_id == 'literature' and arguments.get('source') == 'arxiv' else backend_id
        with self._source_locks[lock_id]:
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
        # OpenAlex and arXiv search concurrently; initialize shared resources once.
        with self._lock:
            backend = self._backend(backend_id)
            if backend_id == 'literature':
                backend.artifact_store
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
        elif backend_id == 'kipris':
            response = backend.search(PatentSearchQuery(query, arguments.get('max_results', 20)),
                                      begin=arguments.get('begin', 1))
        else:
            response = backend.search(PatentSearchQuery(query, arguments.get("max_results", 10)))
        result = {**_response(response, scope="bibliographic_search"), "query": query,
                  "publication_cutoff": self.cutoff or None}
        if backend_id == 'kipris':
            page, size = arguments.get('begin', 1), arguments.get('max_results', 20)
            first = (page - 1) * size + 1
            count = len(response.records)
            more = page * size < response.total_found
            result['coverage'].update(page=page, page_size=size,
                result_range=f'{first}-{first + count - 1}' if count else None,
                more_results_available=more, next_page=page + 1 if more else None)
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
            raise ValueError(f"invalid_integer: expected integer in {schema.get('minimum', 1)}..{schema.get('maximum', 20)}, received {value!r}")
    elif kind == 'boolean' and type(value) is not bool:
        raise ValueError('expected_boolean')
    elif kind == 'array':
        if not isinstance(value, list) or not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 100):
            raise ValueError('invalid_array')
        for item in value:
            _validate(item, schema.get('items', {}), depth + 1)
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
        selected = {name for name in selected if name in {"applicants", "authors", "publication_date", "ipc", "cpc", "container", "family_id", "arxiv_id", "version_updated"}}
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
    "description": 'Term: {type:"term",field:"ta",value:"image matching",match:"all"}. Group: {type:"group",op:"and"|"or"|"not",items:[nodes]}. Term fields: ti,ab,ta,txt,pa,in,pn,ap,pr,ipc,cpc,cl. Match: all/any/exact. Publication-date node: {type:"date_range",field:"pd",begin:"19000101",end:"20240131"}. A date-limited query may omit unknown dates; choose whether an additional unrestricted query is needed. Maximum nesting: 3.',
    "additionalProperties": True,
}
_KIPRIS_SEARCH = _tool(
    'kipris_search',
    'Search Korean patents and utility models in KIPRIS Plus. Prefer short Korean technical keyword queries. Returns bibliographic metadata and abstracts, not claims/full text. Each page costs one of 1000 monthly requests. begin is a 1-based page number.',
    {'query': {'type': 'string', 'maxLength': 500},
     'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 100},
     'begin': {'type': 'integer', 'minimum': 1, 'maximum': 1000}}, ['query'])

_EPO_SEARCH = _tool(
    "epo_search",
    "Search EPO OPS with structured CQL. Provider-default order is not relevance ranking. Inspect coverage/date range and broad_query_sample warnings. Use observed ipc/cpc plus technical terms, date_range partitions, or begin for subsequent pages. Keep each OR branch technically specific; match=any splits words with OR. Returns actual CQL and artifact references.",
    {"query": _QUERY_SCHEMA, "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
     "begin": {"type": "integer", "minimum": 1, "maximum": 2000}},
    ["query"],
)
_EPO_FETCH = _tool(
    "epo_fetch",
    "Fetch an EPO publication constituent: abstract, claims, description or biblio. The family endpoint is not supported by this tool. US claims/description are not supplied by OPS; use biblio/abstract and a source page or identified family publication for full text. Scope failure is not publication absence. EntityNotFound may require number-format verification; never silently drop a fetched candidate.",
    {"publication_number": {"type": "string"}, "constituent": {"type": "string", "enum": ["abstract", "claims", "description", "biblio"]}},
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
for _search_tool in (_EPO_SEARCH, _KIPRIS_SEARCH, _LITERATURE_SEARCH):
    _search_tool['inputSchema']['properties']['max_results'].update(maximum=4, default=3)
    _search_tool['description'] += ' PRISM returns 3 per source by default, maximum 4 per request; choose additional queries/pages only after reviewing results.'
_CAPABILITIES = _tool("search_capabilities", "Report which PRISM search tools are enabled and configured without making a network request.", {}, [])

_SAVE_CANDIDATES = _tool('save_candidates',
    'Persist model-selected shortlist, maximum 15. Merge by identifier/DOI/URL by default; replace=true replaces with your newly ranked shortlist and audits removals. When X covers all essential claim features, supply x_review after source reading: all declared core_features must exactly match mapping.feature rows backed by preserved claims/description/full_text. Accepted X review terminates exploration automatically. Abstract-only X cannot terminate.',
    {'report': {'type': 'object', 'required': ['candidates'], 'additionalProperties': True},
     'replace': {'type': 'boolean'},
     'x_review': {'type': 'object', 'required': ['candidate_id', 'core_features', 'rationale'], 'additionalProperties': False,
                  'properties': {'candidate_id': {'type': 'string', 'description': 'patent:JP7475618B1 or doi:10... or url:https://...'},
                                 'core_features': {'type': 'array', 'minItems': 1, 'maxItems': 30, 'items': {'type': 'string'}},
                                 'rationale': {'type': 'string', 'maxLength': 4000}}}}, ['report'])
_SAVE_CANDIDATES['annotations'].update(readOnlyHint=False, openWorldHint=False)
_SOURCE_FETCH = _tool('source_fetch',
    'Read and preserve a public HTTPS source page or PDF with evidence_refs. No login, no TLS bypass. Prefer a known canonical source URL to a failing redirect. section=claims extracts labelled Google Patents claims; section=page retains description, family and citation tables. offset reads later text windows from the same capture. Identity is confirmed only when parsed from the page; other pages match by URL.',
    {'url': {'type': 'string', 'maxLength': 4000},
     'section': {'type': 'string', 'enum': ['claims', 'page']},
     'offset': {'type': 'integer', 'minimum': 0, 'maximum': 1000000},
     'max_chars': {'type': 'integer', 'minimum': 1000, 'maximum': 24000}}, ['url'])
_CITATION_SEARCH = _tool('citation_search',
    'Retrieve one hop of backward (cited) or forward (citing) publications for an exact patent number, DOI or openalex:W identifier. Uses enabled EPO/OpenAlex APIs. Does not union families: inspect the family/source page and select additional family identifiers yourself. Citation adjacency never proves claim similarity. begin pages patent forward results.',
    {'identifier': {'type': 'string'}, 'direction': {'type': 'string', 'enum': ['backward', 'forward']},
     'begin': {'type': 'integer', 'minimum': 1, 'maximum': 2000}}, ['identifier', 'direction'])

_START_COLLECTION = _tool('start_collection',
    'Start independent EPO, KIPRIS, OpenAlex and arXiv requests in background, returning immediately. Include arxiv_query for direct arXiv search alongside openalex_query. Supply source-specific model-written queries for all useful available sources. Results use literature_search for OpenAlex and arxiv_search for arXiv. Run native web search while these requests run; then collect_results. Default 3, maximum 4 results per source. One active round at a time; no automatic query planning.',
    {'epo_query': _QUERY_SCHEMA, 'kipris_query': {'type': 'string', 'maxLength': 500},
     'openalex_query': {'type': 'string', 'maxLength': 500},
     'arxiv_query': {'type': 'string', 'maxLength': 500},
     'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 4}}, [])
_COLLECT_RESULTS = _tool('collect_results',
    'Nonblocking snapshot of the current parallel collection. Read completed results and continue useful work while any sources are pending. Results stay in journal even if this session ends.', {}, [])


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
                value["budget"] = tools.budget()
                _reply(request_id, {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                                    "structuredContent": value, "isError": error})
            else:
                _reply(request_id, error={"code": -32601, "message": "Method not found"})
        except (ValueError, TypeError):
            _reply(request_id, error={"code": -32700, "message": "Invalid JSON-RPC request"})

if __name__ == "__main__":
    main()
