"""Bounded arxiv.py requests with preserved Atom evidence, not PDF full text."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from . import parsers
from .base import (EvidenceRef, FieldValue, PatentRecord, PatentSearchError,
                   PatentSearchResponse, SOURCE_NORMALIZED, TRANSLATION_UNKNOWN)

PROFILE = "arxiv_atom_v1"
NS = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/"}
MAX_BYTES = 2_000_000


def normalize_id(value):
    value = re.sub(r"^(?:https?://arxiv.org/abs/|10\.48550/arxiv\.|arxiv:)", "", str(value).strip(), flags=re.I)
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.-]+/\d{7})(?:v\d+)?", value, re.I):
        raise PatentSearchError("invalid_arxiv_id")
    return value.lower()


def parse(body):
    if len(body) > MAX_BYTES or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise PatentSearchError("unsafe_or_oversize_arxiv_feed")
    try:
        root = ET.fromstring(body)
        total_text = root.findtext("o:totalResults", default="", namespaces=NS)
        total = int(total_text) if total_text else None
        records = []
        for entry in root.findall("a:entry", NS):
            ident = normalize_id(entry.findtext("a:id", default="", namespaces=NS))
            base = re.sub(r"v\d+$", "", ident)
            text = lambda path: " ".join(entry.findtext(path, default="", namespaces=NS).split())
            fields = {"title": text("a:title"), "abstract": text("a:summary"),
                      "authors": "; ".join(" ".join(e.text.split()) for e in entry.findall("a:author/a:name", NS) if e.text),
                      "publication_date": text("a:published")[:10],
                      "arxiv_id": ident, "version_updated": text("a:updated"),
                      "categories": "; ".join(e.get("term", "") for e in entry.findall("a:category", NS))}
            records.append(("10.48550/arxiv." + base, fields))
        return total, records
    except (ET.ParseError, ValueError) as exc:
        raise PatentSearchError("invalid_arxiv_feed") from exc


def _extract(body, path):
    index, field = path.split("/", 1)
    try:
        return parse(body)[1][int(index)][1][field]
    except (IndexError, KeyError, ValueError) as exc:
        raise parsers.FieldPathMissing(path) from exc


_REGISTERED = False


def register():
    # 프로세스당 한 번. literature_parser.register 가 먼저 부르고, materialize 가
    # 응답마다 다시 부른다 — 확인 없이 두면 정상 응답이 전부 ParserError 가 된다.
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True
    parsers.register_parser("arxiv_atom", "1", _extract)
    parsers.register_profile(parsers.SourceProfile(profile_id=PROFILE, parser_id="arxiv_atom", parser_version="1",
        source_kind=SOURCE_NORMALIZED, translation_state=TRANSLATION_UNKNOWN, language="", raw_capable=False,
        note="arXiv preprint metadata and abstract. published is first submission, not journal publication or peer review."))


def materialize(body, store, url="", status=200):
    register()
    aid = store.put(body)
    total, entries = parse(body)
    records = []
    for i, (doi, values) in enumerate(entries):
        fields = {name: FieldValue(value, EvidenceRef(aid, f"{i}/{name}", PROFILE)) for name, value in values.items() if value}
        records.append(PatentRecord(doc_number=doi, title=values["title"], fields=fields,
                                   source_url="https://arxiv.org/abs/" + values["arxiv_id"]))
    return PatentSearchResponse(records=tuple(records), total_found=total if total is not None else len(records),
        raw_artifact_id=aid, fetched_at=datetime.now(timezone.utc).isoformat(), http_status=status, request_url=url,
        source_stats=({"source": "arxiv", "total_results": total, "returned_records": len(records), "status": "ok" if records else "zero_results"},),
        notes=("arXiv: preprint metadata/abstract only; publication_date is first submission. PDF full text and peer review are not verified.",))


class ArxivBackend:
    def __init__(self, store):
        self.store = store

    def query(self, query="", *, identifier="", rows=5):
        import arxiv
        import requests
        from ..config import PATHS
        from ..search_mcp_server import _quota_lock
        captured = []

        class Session(requests.Session):
            def get(self, url, **kwargs):
                response = super().get(url, **kwargs, timeout=20, stream=True)
                try:
                    data = bytearray()
                    for chunk in response.iter_content(65536):
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise PatentSearchError("arxiv_response_too_large")
                    response._content = bytes(data)
                    captured.append((bytes(data), response.url, response.status_code))
                    return response
                finally:
                    response.close()

        # Serialize and space calls across AGY/Codex MCP processes as well as this process.
        with _quota_lock("arxiv-mcp.lock"):
            stamp = PATHS.data_dir / "arxiv-last-request.txt"
            try:
                last = float(stamp.read_text())
            except (OSError, ValueError):
                last = 0
            time.sleep(max(0, min(3, 3 - (time.time() - last))))
            client = arxiv.Client(page_size=max(1, min(rows, 20)), delay_seconds=3, num_retries=0)
            client._session.close()
            client._session = Session()  # arxiv 4.0.1 has no public timeout/raw-response hook.
            # requests 의 certifi 목록은 이 PC 의 사내 TLS 재서명을 믿지 않는다.
            # 검증은 켠 채 Windows 인증서 저장소를 쓴다(openalex_client 와 같다).
            from .openalex_client import trust_store_adapter
            client._session.mount("https://", trust_store_adapter())
            try:
                search = arxiv.Search(id_list=[normalize_id(identifier)], max_results=1) if identifier else arxiv.Search(query=query, max_results=max(1, min(rows, 20)))
                list(client.results(search))
                if not captured:
                    raise PatentSearchError("arxiv_missing_response")
                body, url, status = captured[0]
                response = materialize(body, self.store, url, status)
                if identifier and any(normalize_id(r.fields["arxiv_id"].value) != normalize_id(identifier)
                                      and re.sub(r"v\d+$", "", normalize_id(r.fields["arxiv_id"].value)) != normalize_id(identifier)
                                      for r in response.records):
                    raise PatentSearchError("arxiv_identifier_mismatch")
                return response
            except (arxiv.ArxivError, requests.RequestException) as exc:
                if captured:
                    self.store.put(captured[-1][0])
                raise PatentSearchError("arxiv_request_failed: " + type(exc).__name__) from exc
            finally:
                client._session.close()
                stamp.write_text(str(time.time()))
