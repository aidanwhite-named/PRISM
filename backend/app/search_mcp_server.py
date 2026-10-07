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
from . import search_dates, search_channels, search_manifest, settings_service
from .config import PATHS
from .db import session_scope
from .patent_search import get_backend, epo_cql
from .patent_search.base import PatentSearchError, PatentSearchQuery
from .patent_search.epo_client import scrub, credential_tokens, MAX_RESULTS_PER_QUERY, MAX_SEARCH_RESULTS

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



def error_response(exc, name, arguments, secrets=()):
    """Keep the OPS fault, but explain which scope failed and the next options."""
    value = {"error_code": getattr(exc, "fault_code", "") or type(exc).__name__,
             "detail": scrub(str(exc), *secrets)[:500]}
    args = arguments if isinstance(arguments, dict) else {}
    if name == 'kiwee_search':
        value['not_evidence_of_absence'] = True
        value['http_status'] = getattr(exc, 'http_status', None)
        value['recovery'] = {'next_step': 'Check the Kiwee trial search in Settings for TLS, client certificate, permissions or query compatibility. Continue with other available sources; a failed request is not zero results.'}
    if name == "epo_search":
        value["not_evidence_of_absence"] = True
        value["recovery"] = {
            "page_size": {"minimum": 1, "maximum": MAX_RESULTS_PER_QUERY},
            "next_step": "Use at most 100 results per call, then coverage.next_begin with the same query for another page. Each AND/OR/NOT group has type=group, op and items; each term has type=term, field and value. Do not change the search meaning to repair syntax.",
            "query_example": {"type": "group", "op": "and", "items": [
                {"type": "term", "field": "ta", "value": "speech"},
                {"type": "term", "field": "ta", "value": "noise reduction"}]},
        }
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
    coverage["retrieval_limit_reached"] = bool(total and total > MAX_SEARCH_RESULTS and begin + returned > MAX_SEARCH_RESULTS)
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
    def __init__(self, *, values=None, work_dir=None, cutoff=None):
        self.work_dir = Path(work_dir or os.environ["PRISM_SEARCH_WORK_DIR"])
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.work_dir / LEDGER_NAME
        self.calls = sum(row.get("state") == "started" for row in search_manifest.read_tool_journal(self.work_dir))
        self.cutoff = search_dates.normalize_cutoff(cutoff if cutoff is not None else os.environ.get("PRISM_SEARCH_CUTOFF", ""))
        if values is None:
            with session_scope() as session:
                values = settings_service.get_all(session)
        self.values = values
        self.backends = {}
        self._lock = threading.RLock()
        self._source_locks = {name: threading.RLock() for name in ('epo', 'literature', 'kipris', 'kiwee', 'arxiv')}
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
            # Windows antivirus/indexing can briefly hold a freshly written journal.
            for attempt in range(6):
                try:
                    handle = self.ledger_path.open("a", encoding="utf-8")
                    break
                except PermissionError:
                    if os.name != 'nt' or attempt == 5:
                        raise
                    time.sleep(.02 * (attempt + 1))
            with handle:
                handle.write(serialized + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def statuses(self):
        statuses = search_channels.availability(self.values)
        for source, detail in self.disabled_sources.items():
            statuses[source] = {**statuses.get(source, {}), 'status': 'unavailable', 'detail': detail}
        return statuses

    def budget(self):
        result = {'used': self.calls}
        try:
            deadline = float((self.work_dir / 'search_deadline.json').read_text(encoding='utf-8'))
            result['seconds_remaining'] = max(0, round(deadline - time.time(), 1))
        except (OSError, ValueError):
            pass
        return result

    def phase(self):
        try:
            return json.loads((self.work_dir / 'search_phase.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return 'searching'

    def tool_definitions(self):
        statuses = self.statuses()
        return [_CAPABILITIES, _SAVE_FINDINGS, _SOURCE_FETCH, _CITATION_SEARCH] + [
            tool for tool in (_EPO_SEARCH, _EPO_FETCH, _LITERATURE_SEARCH, _LITERATURE_FETCH, _KIPRIS_SEARCH, _KIWEE_SEARCH)
            if statuses[tool['name'].split('_')[0]]['status'] == 'available']

    def call(self, name: str, arguments: dict) -> dict:
        with self._lock:
            return_row = self._reserve_call(name, arguments)
        return self._run_call(name, arguments, return_row)

    def _reserve_call(self, name, arguments):
        row = {"id": str(uuid.uuid4()), "tool": name, "arguments": arguments, "sequence": self.calls}
        self.calls += 1
        row["sequence"] = self.calls
        self._record({**row, "state": "started"})
        return row

    def _run_call(self, name, arguments, row):
        failed_findings = []
        try:
            definition = next((tool for tool in self.tool_definitions() if tool["name"] == name), None)
            if definition is None:
                raise ValueError("tool_unavailable")
            schema = definition["inputSchema"]
            if name == 'save_findings':
                # Check the batch envelope here; individual documents are
                # validated and saved independently by the storage handler.
                schema = copy.deepcopy(schema)
                schema['properties']['records']['items'] = {}
            _validate(arguments, schema)
            if name == 'save_findings':
                accepted = []
                item_schema = definition['inputSchema']['properties']['records']['items']
                for position, raw in enumerate(arguments['records']):
                    try:
                        _validate(raw, item_schema)
                        accepted.append(raw)
                    except ValueError as exc:
                        failed_findings.append({'index': position,
                            'title': raw.get('title', '') if isinstance(raw, dict) else '',
                            'url': raw.get('url', '') if isinstance(raw, dict) else '',
                            'code': 'invalid_finding_shape', 'detail': str(exc)})
                arguments = {**arguments, 'records': accepted}
            from .search_engine import input_documents
            target = input_documents.fetch_target(name, arguments)
            if target and input_documents.is_input(self.work_dir, target):
                input_documents.record_exclusion(self.work_dir, target)
                result = {'records': [], 'excluded_input_document': True,
                          'detail': '입력으로 제공한 동일 문헌입니다. 추가 조회·검증과 후보 저장을 생략하고 다른 문헌을 탐색하세요.'}
                self._record({**row, 'state': 'completed', 'ok': True, 'result': result})
                return result
            if name not in ("search_capabilities", "save_findings") and self.budget().get('seconds_remaining', 1) <= 0:
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
                            and (name != 'literature_fetch' or arguments.get('constituent') != 'full_text'
                                 or result.get('verification_scope') == 'full_text')
                            and (name != 'source_fetch' or result.get('capture_version') == 2)
                            and result.get("identifier_matched") is not False):
                        result = copy.deepcopy(result)
                        result["budget"] = self.budget()
                        result["reused_from_call_id"] = previous["id"]
                        self._record({**row, "state": "completed", "ok": True, "result": result})
                        return result
            if name == "search_capabilities":
                result = {"tools": self.statuses(), "publication_cutoff": self.cutoff or None,
                          "tool_calls_used": self.calls}
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
            if failed_findings:
                result.setdefault('failed_findings', []).extend(failed_findings)
        except Exception as exc:
            if name == 'kipris_search' and getattr(exc, 'fault_code', '') in ('KIPRIS.30', 'KIPRIS.31'):
                self.disabled_sources['kipris'] = str(exc)
            self._record({**row, "state": "completed", "ok": False,
                          **error_response(exc, name, arguments, self.secrets)})
            raise
        if result.get('records'):
            original = result['records']
            result['records'] = input_documents.filter_records(self.work_dir, original)
            if len(result['records']) != len(original):
                result['excluded_input_documents'] = [
                    {k: r.get(k, '') for k in ('title', 'url', 'document_number')}
                    for r in original if input_documents.is_input(self.work_dir, r)]
                result['scope_note'] = '입력과 동일한 문헌은 후보에서 제외했습니다. 다른 문헌만 선별·검증하세요.'
        result["budget"] = self.budget()
        self._record({**row, "state": "completed", "ok": True, "result": result})
        if name in ('source_fetch', 'literature_fetch', 'epo_fetch') and result.get('records'):
            # Reconcile aliases after the source receipt is durable.
            from .search_engine import autonomous_store
            autonomous_store.merge(self.work_dir, [])
        return result

    def _execute(self, name, arguments):
        if name == 'save_findings':
            from .search_engine.autonomous_store import save_findings
            return save_findings(self, arguments)
        if name in ('source_fetch', 'citation_search'):
            from . import search_source_tools
            return getattr(search_source_tools, name)(self, arguments)
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
        corrections = []
        node = _query_node(arguments["query"], corrections=corrections)
        normalized = []
        # Do not silently hide unknown publication dates with a DB-side cutoff.
        cql = epo_cql.build(node, normalized=normalized)
        begin = arguments.get("begin", 1)
        paging = {"begin": begin} if begin != 1 else {}
        response = self._backend("epo").search_structured(node, max_results=arguments.get("max_results", 10), **paging)
        return epo_search_advice({**_response(response, scope="bibliographic_search"), "cql": cql,
                "normalized_classifications": normalized, "query_normalizations": corrections,
                "publication_cutoff": self.cutoff or None}, begin)

    def _plain_search(self, backend_id, arguments):
        query = arguments["query"]
        if backend_id == 'literature' and arguments.get('source') == 'openreview':
            if arguments.get('cites_doi'):
                raise ValueError('cites_doi_requires_openalex')
            from .search_engine.paper_repositories import openreview_search
            return openreview_search(self, arguments)
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
        elif backend_id == 'kiwee':
            response = backend.search(PatentSearchQuery(query, arguments.get('max_results', 10)),
                begin=arguments.get('begin', 1), query_mode=arguments.get('query_mode', 'keywords'))
        else:
            response = backend.search(PatentSearchQuery(query, arguments.get("max_results", 10)))
        result = {**_response(response, scope="bibliographic_search"), "query": query,
                  "publication_cutoff": self.cutoff or None}
        if backend_id in ('kipris', 'kiwee'):
            page, size = arguments.get('begin', 1), arguments.get('max_results', 20 if backend_id == 'kipris' else 10)
            first = (page - 1) * size + 1
            count = len(response.records)
            more = page * size < response.total_found
            result['coverage'].update(page=page, page_size=size,
                result_range=f'{first}-{first + count - 1}' if count else None,
                more_results_available=more, next_page=page + 1 if more else None)
            if backend_id == 'kiwee':
                from .patent_search.kiwee_client import build_query
                result['solr_query'] = build_query(query, arguments.get('query_mode', 'keywords'))
                raw_count = response.source_stats[0]['returned_records']
                result['coverage']['server_records_in_page'] = raw_count
                result['coverage']['result_range'] = f'{first}-{first + raw_count - 1}' if raw_count else None
                result['coverage']['source_stats'][0]['result_range'] = result['coverage']['result_range']
                if raw_count and not count:
                    result['coverage']['status'] = 'unidentified_records'
        return result

    def _fetch(self, backend_id, arguments, identifier_key):
        identifier = arguments[identifier_key]
        constituent = arguments.get("constituent", "abstract" if backend_id == "literature" else "claims")
        if backend_id == 'literature' and constituent == 'full_text':
            from .search_engine.source_acquisition import article_full_text
            return article_full_text(self, arguments)
        backend = self._backend(backend_id)
        response = backend.fetch_document(identifier, constituent)
        result = _response(response, scope=constituent)
        def identity(number):
            return search_manifest.identity_key(doi=number) if backend_id == "literature" else search_manifest.identity_key(number)
        result["requested_identifier"] = identifier
        result["identifier_matched"] = any(identity(record["document_number"]) == identity(identifier) for record in result["records"])
        if backend_id == 'epo':
            from .search_engine.source_acquisition import ops_capture
            result = ops_capture(result, constituent)
        return result

def _validate(value, schema, depth=0):
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                _validate(value, branch, depth)
                return
            except ValueError:
                pass
        raise ValueError('invalid_cql_node: use {type:"term",field,value}, {type:"group",op,items}, or {type:"date_range",field:"pd",begin,end}; do not mix node fields')
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
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float('inf')):
            raise ValueError("invalid_string")
    elif kind == "integer":
        if type(value) is not int or not schema.get("minimum", 1) <= value <= schema.get("maximum", 20):
            raise ValueError(f"invalid_integer: expected integer in {schema.get('minimum', 1)}..{schema.get('maximum', 20)}, received {value!r}")
    elif kind == 'boolean' and type(value) is not bool:
        raise ValueError('expected_boolean')
    elif kind == 'array':
        if not isinstance(value, list) or not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', float('inf')):
            raise ValueError('invalid_array')
        for item in value:
            _validate(item, schema.get('items', {}), depth + 1)
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"invalid_enum: expected {json.dumps(schema['enum'], ensure_ascii=False)}, received {json.dumps(value, ensure_ascii=False)}")

def _query_node(raw: Any, depth=0, *, corrections=None, path="query"):
    if depth > epo_cql.MAX_DEPTH or not isinstance(raw, dict):
        raise ValueError("invalid_cql_structure_or_depth")
    kind = raw.get("type")
    if kind is None:
        # Only fill an unambiguous discriminator; never infer an operator or terms.
        keys = set(raw)
        if keys == {"op", "items"}:
            kind = "group"
        elif {"begin", "end"} <= keys <= {"field", "begin", "end"}:
            kind = "date_range"
        elif {"field", "value"} <= keys <= {"field", "value", "match"}:
            kind = "term"
        else:
            raise ValueError('missing_cql_type: specify type=group with op/items, term with field/value, or date_range with begin/end')
        if corrections is not None:
            corrections.append({"path": path + ".type", "inferred": kind})
    if kind == "term":
        if set(raw) - {"type", "field", "value", "match"}:
            raise ValueError("unknown_cql_term_field")
        return epo_cql.Term(field=raw.get("field", ""), value=raw.get("value", ""),
                            match=raw.get("match", epo_cql.MATCH_ALL))
    if kind == "group":
        items = raw.get("items")
        if set(raw) - {"type", "op", "items"} or not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise ValueError("invalid_cql_group")
        return epo_cql.Group(op=raw.get("op", ""), items=tuple(
            _query_node(item, depth+1, corrections=corrections, path=f"{path}.items[{i}]")
            for i, item in enumerate(items)))
    if kind == "date_range":
        if set(raw) - {"type", "field", "begin", "end"}:
            raise ValueError("unknown_cql_date_field")
        return epo_cql.DateRange(field=raw.get("field", "pd"),
                                 begin=raw.get("begin", ""), end=raw.get("end", ""))
    raise ValueError("unsupported_cql_type")

def _response(response, *, scope: str) -> dict:
    from .search_engine.source_observations import record_scopes
    records = [_record(record) for record in response.records]
    obtained = list(dict.fromkeys(s for r in records for s in record_scopes(r)))
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
        "requested_scope": scope,
        "verification_scope": scope if is_search else (
            obtained[0] if len(obtained) == 1 else "mixed" if obtained else "not_available"),
        "total_found": response.total_found,
        "raw_artifact_id": response.raw_artifact_id,
        "fetched_at": response.fetched_at,
        "http_status": response.http_status,
        "request_url": response.request_url,
        "notes": list(response.notes),
        "failed_sources": list(response.failed_sources),
        "records": records,
        "coverage": coverage,
        "available_fields": sorted({k.split(":")[0] for r in response.records for k in r.fields}),
        "scope_note": "Bibliographic search/abstract is not claims or full-text verification." if is_search else "Only returned fields were obtained.",
    }


def _record(record) -> dict:
    fields = {}
    evidence = {}
    for name, field in record.fields.items():
        fields[name] = field.value
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


def _query_schema(depth=0):
    def node(properties, required):
        return {"type": "object", "properties": properties, "required": required,
                "additionalProperties": False}
    choices = [node({
        "type": {"type": "string", "enum": ["term"]},
        "field": {"type": "string", "enum": list(epo_cql.ALLOWED_FIELDS)},
        "value": {"type": "string", "minLength": 1, "maxLength": epo_cql.MAX_VALUE_CHARS},
        "match": {"type": "string", "enum": list(epo_cql.MATCH_KINDS)},
    }, ["field", "value"]), node({
        "type": {"type": "string", "enum": ["date_range"]},
        "field": {"type": "string", "enum": ["pd"]},
        "begin": {"type": "string", "minLength": 8, "maxLength": 8},
        "end": {"type": "string", "minLength": 8, "maxLength": 8},
    }, ["begin", "end"])]
    if depth < epo_cql.MAX_DEPTH:
        choices.append(node({
            "type": {"type": "string", "enum": ["group"]},
            "op": {"type": "string", "enum": list(epo_cql.OPERATORS)},
            "items": {"type": "array", "minItems": 1, "maxItems": 20,
                      "items": _query_schema(depth + 1)},
        }, ["op", "items"]))
    return {"anyOf": choices}


_QUERY_SCHEMA = {**_query_schema(), "description":
    'Include type=term for field/value, type=group for op/items (AND/OR/NOT), or type=date_range for pd/begin/end (YYYYMMDD). Missing type is accepted only when the shape is unambiguous. NOT needs exactly two items. Maximum nesting: 3. Date filtering can omit unknown publication dates; consider a separate unrestricted search.'}
_KIWEE_SEARCH = _tool(
    'kiwee_search',
    'Experimental Kiwee Solr gateway search. keywords mode ANDs words, each across title(tl), abstract(ab), claims(cl). solr mode sends native Lucene syntax, NOT Kiwee UI syntax. begin is a 1-based page number; use coverage.next_page. Defaults to configured country shards (initially KR). Returned fields have unverified source/translation status. Authentication or TLS errors are not zero results. Use source_fetch or other sources to verify full text.',
    {'query': {'type': 'string', 'maxLength': 2000},
     'query_mode': {'type': 'string', 'enum': ['keywords', 'solr']},
     'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 50},
     'begin': {'type': 'integer', 'minimum': 1, 'maximum': 10000}}, ['query'])

_KIPRIS_SEARCH = _tool(
    'kipris_search',
    'Search Korean patents and utility models in KIPRIS Plus. Prefer short Korean technical keyword queries. Returns bibliographic metadata and abstracts, not claims/full text. Each page costs one of 1000 monthly requests. begin is a 1-based page number.',
    {'query': {'type': 'string', 'maxLength': 500},
     'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 100},
     'begin': {'type': 'integer', 'minimum': 1, 'maximum': 1000}}, ['query'])

_EPO_SEARCH = _tool(
    "epo_search",
    "Search EPO OPS with structured CQL. Provider-default order is not relevance ranking. Inspect coverage/date range and broad_query_sample warnings. Use observed ipc/cpc plus technical terms, date_range partitions, or begin for subsequent pages. Keep each OR branch technically specific; match=any splits words with OR. Returns actual CQL and artifact references.",
    {"query": _QUERY_SCHEMA, "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS_PER_QUERY,
        "description": "Results per page (default 10), at most 100. Request additional pages using coverage.next_begin; do not repeat begin=1."},
     "begin": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_RESULTS}},
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
    {"query": {"type": "string", "minLength": 1, "maxLength": 500}, "max_results": {"type": "integer", "minimum": 1, "maximum": 20}, "source": {"type": "string", "enum": ["crossref_epmc", "openalex", "arxiv", "openreview"]}, "openalex_mode": {"type": "string", "enum": ["search", "title_and_abstract"]}, "cites_doi": {"type": "string", "minLength": 1, "maxLength": 200}},
    ["query"],
)
_LITERATURE_SEARCH['description'] += (' source=openreview searches public paper titles through the OpenReview API without Semantic Scholar. '
    'openreview_mode=exact_title searches an identified full title; default title_terms is broader and may match only some query terms. '
    'Returns the paper note and its own PDF/HTML links; read chosen PDFs using source_fetch. '
    'If OpenAlex has no record or usable body, search arXiv/OpenReview and public conference/author repositories; API failure is not absence.')
_LITERATURE_SEARCH['inputSchema']['properties']['openreview_mode'] = {
    'type': 'string', 'enum': ['title_terms', 'exact_title']}
_LITERATURE_FETCH = _tool(
    "literature_fetch",
    "Fetch evidence for an exact DOI. abstract uses Europe PMC, then OpenAlex, then Crossref; biblio uses Crossref/OpenAlex. arXiv IDs are accepted as 10.48550/arXiv.<id>, arXiv:<id> or arxiv.org/abs/<id> (version suffix ignored for identity). Mismatched identities are rejected. full_text uses OpenAlex public copy locations as described below.",
    {"doi": {"type": "string"}, "constituent": {"type": "string", "enum": ["abstract", "biblio", "full_text"]},
     "find": {"type": "string", "minLength": 1, "maxLength": 200},
     "title": {"type": "string", "minLength": 1, "maxLength": 500},
     "max_chars": {"type": "integer", "minimum": 1000, "maximum": 24000}},
    ["doi"],
)
_LITERATURE_FETCH['description'] += (' constituent=full_text resolves the exact DOI via OpenAlex best_oa_location/locations, '
    'then tries up to four public HTTPS copies within the remaining budget, using source_fetch. '
    'Use this when publisher/ResearchGate/DOI access fails. captured_source contains capture_artifact_id and passage_options '
    'for observed body evidence; public_copies alone and metadata/abstract are not body evidence. '
    'For arXiv identifiers it reads official arXiv HTML/PDF directly, independently of OpenAlex. '
    'Optional title helps independent discovery if OpenAlex cannot resolve the DOI. '
    'When body acquisition fails, follow fallback_searches using your web search or literature_search tools, '
    'confirm each candidate identity/version, and keep distinct preprint/journal DOI records distinct until explicit source evidence links them.')
_EPO_FETCH['description'] += (' Actual returned claims/description include capture_artifact_id and passage_options for '
    'save_findings review, so OPS body text can be used directly if a Google Patents page is blocked. '
    'Exact publication identity is preserved; another family publication is a separate candidate.')
_CAPABILITIES = _tool("search_capabilities", "Report which PRISM search tools are enabled and configured without making a network request.", {}, [])

_SAVE_FINDINGS = _tool('save_findings',
    'Save useful findings immediately, including provisional leads before source checking. '
    'ranked=true places these findings first in your relevance order; other saved leads remain. '
    'reported_scope describes what you actually inspected; storage does not certify that assessment.',
    {'records': {'type': 'array', 'items': {'type': 'object', 'required': ['title', 'url', 'reason'],
        'additionalProperties': False, 'properties': {key: {'type': 'string'} for key in
        ('title', 'url', 'document_number', 'reason', 'difference', 'reported_scope', 'publication_date', 'authors')}}},
     'ranked': {'type': 'boolean'}}, ['records'])
_SAVE_FINDINGS['annotations'].update(readOnlyHint=False, openWorldHint=False)
_finding_fields = _SAVE_FINDINGS['inputSchema']['properties']['records']['items']['properties']
_finding_fields.update({key: {'type': 'string'} for key in ('triage_status', 'triage_reason', 'core_matches', 'review_stage')})
_finding_fields['triage_status'] = {'type': 'string',
    'enum': ['unreviewed', 'candidate', 'promising', 'hold', 'rejected', 'detailed'],
    'description': 'promising: 확인한 초기 자료에서 전체 구조와 핵심 관계가 매우 가깝지만 본문 검증이 미완료인 유력 후보. X/Y/Z와 별도의 잠정 판단.'}
_finding_fields['review'] = {'type': 'object', 'additionalProperties': False,
    'required': ['verdict', 'reason', 'gaps', 'queries', 'passages'], 'properties': {
        'verdict': {'type': 'string', 'enum': ['strong', 'partial', 'mismatch', 'unavailable']},
        'group': {'type': 'string', 'enum': ['X', 'Y', 'Z']},
        'reason': {'type': 'string'}, 'gaps': {'type': 'string'},
        'queries': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string'}},
        'passages': {'type': 'array', 'maxItems': 12, 'items': {'type': 'object',
            'additionalProperties': False, 'required': ['capture_artifact_id', 'feature', 'relation', 'translation'],
            'properties': {key: {'type': 'string'} for key in
                ('capture_artifact_id', 'passage_id', 'quote', 'feature', 'relation', 'translation')}}}}}
_finding_fields['review']['properties']['component_matches'] = {
    'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['component_id', 'verdict', 'reason', 'gaps', 'passage_indices'],
        'properties': {'component_id': {'type': 'string'},
            'verdict': {'type': 'string', 'enum': ['strong', 'partial', 'mismatch', 'not_found', 'unavailable']},
            'reason': {'type': 'string'}, 'gaps': {'type': 'string'},
            'reviewed_capture_ids': {'type': 'array', 'items': {'type': 'string'},
                'description': 'For not_found, exact capture_artifact_id values of inspected candidate body text. Absence is limited to these inspected windows.'},
            'passage_indices': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0, 'maximum': 11}}}}}
_SAVE_FINDINGS['description'] += (' Reviews use exact '
    'capture_artifact_id and passage_id selected from source_fetch.passage_options (preferred), '
    'or one exact continuous quote. Never use ellipses, paraphrase, case changes or joined sentences in quote. '
    'Translate the entire selected passage. repair_options contains observed passages available for optional correction of pending_source_checks. '
    'Reviews compare technical features AND relations/conditions; unavailable is not mismatch. '
    'Set review.group to X (strong overall structure and core), Y (different overall structure but strong core), '
    'or Z (similar overall structure, partial core). Omit group for unverified or irrelevant sources. '
    'X/Y use verdict=strong; Z uses partial. Explain both structure and core relations in reason. '
    'X/Y/Z are output groups, not search-order requirements or automatic stop conditions. '
    'The response returns pending source checks and search gaps. Quote validation is not semantic certification.')
_SAVE_FINDINGS['description'] += (' For gap search (focus.mode=gap), omit group and save component_matches '
    'for the selected components actually reviewed, with independent verdict/reason/gaps and zero-based passage_indices into review.passages. '
    'Omitted components remain unreviewed and do not require another run. not_found requires reviewed_capture_ids for actual inspected body windows, '
    'a scope-limited reason and missing limitations in gaps; unavailable is only for actual inability to obtain/read sources, never skipped review. '
    'The whole claim is context, not a requirement that every component match. Observed source evidence remains required per component.')

_SOURCE_FETCH = _tool('source_fetch',
    'Read and preserve a public HTTPS source page or PDF with evidence_refs. No login, no TLS bypass. Prefer a known canonical source URL to a failing redirect. section=claims or description extracts labelled Google Patents text; section=page retains page text, while a labelled arXiv article body is extracted as full_text. offset reads later windows; find locates an exact term with preceding context from the cached capture, saving tokens. Exact DOI/arXiv/publication identifiers and captured citation metadata/canonical/PDF links connect source URLs. Landing pages do not qualify as full_text. Search, source reading and review saving may be interleaved freely.',
    {'url': {'type': 'string', 'maxLength': 4000},
     'section': {'type': 'string', 'enum': ['claims', 'description', 'page']},
     'offset': {'type': 'integer', 'minimum': 0, 'maximum': 1000000},
     'find': {'type': 'string', 'minLength': 1, 'maxLength': 200},
     'max_chars': {'type': 'integer', 'minimum': 1000, 'maximum': 24000}}, ['url'])
_CITATION_SEARCH = _tool('citation_search',
    'Retrieve one hop of backward (cited) or forward (citing) publications for an exact patent number, DOI or openalex:W identifier. Uses enabled EPO/OpenAlex APIs. Does not union families: inspect the family/source page and select additional family identifiers yourself. Citation adjacency never proves claim similarity. begin pages patent forward results.',
    {'identifier': {'type': 'string'}, 'direction': {'type': 'string', 'enum': ['backward', 'forward']},
     'begin': {'type': 'integer', 'minimum': 1, 'maximum': 2000}}, ['identifier', 'direction'])

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
        line = sys.stdin.readline()
        if not line:
            break
        request_id = None
        try:
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
