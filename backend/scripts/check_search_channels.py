"""Small live search diagnostic using the application's tools and quota ledger."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import PATHS
from app import search_manifest, search_verification
from app.search_mcp_server import SearchTools, error_response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    folder = PATHS.data_dir / "diagnostics" / ("channels-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    tools = SearchTools(work_dir=folder, max_calls=8)
    summary = {"checked_at": datetime.now(timezone.utc).isoformat(), "calls": [],
               "qualification": "Connectivity and returned evidence only; not relevance or recall benchmark."}

    def call(name, args):
        started = time.monotonic()
        row = {"tool": name, "arguments": args}
        try:
            result = tools.call(name, args)
            row.update(http_status=result.get("http_status"), coverage=result.get("coverage"),
                       failed_sources=result.get("failed_sources"),
                       records=[{"document_number": r["document_number"], "title": r["title"],
                                 "fields": list(r["fields"]), "passages": len(r.get("evidence_passages", []))}
                                for r in result.get("records", [])])
            checks = []
            for record in result.get("records", []):
                selectors = record.get("evidence_passages") or []
                if not selectors:
                    continue
                number = record["document_number"]
                candidate = {"doi" if number.startswith("10.") else "doc_number": number,
                             "mapping": [{"evidence_passage_id": selectors[0]["passage_id"]}]}
                reported, _ = search_manifest.parse(json.dumps({"candidates": [candidate]}))
                verified = search_verification.verify(reported, {}, [{
                    "tool": name, "state": "completed", "ok": True, "result": result,
                }])["candidates"][0]["mapping"][0]
                checks.append({"document": number, "support_verified": verified["support_verified"],
                               "passage_resolved": bool(verified.get("passage_resolution"))})
            row["passage_checks"] = checks
            row["passage_check_meaning"] = "First passage selected programmatically to test evidence delivery, not model judgment."
        except Exception as exc:
            row.update(error_response(exc, name, args, tools.secrets))
            result = {}
        row["elapsed_seconds"] = round(time.monotonic() - started, 2)
        summary["calls"].append(row)
        (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return result

    print(json.dumps({"folder": str(folder)}, ensure_ascii=False), flush=True)
    epo = call("epo_search", {"query": {"type": "term", "field": "ta", "value": "joint rotation", "match": "all"}, "max_results": 3})
    if epo.get("records"):
        call("epo_fetch", {"publication_number": epo["records"][0]["document_number"], "constituent": "abstract"})
    alex = call("literature_search", {"query": "skeletal animation", "source": "openalex", "openalex_mode": "title_and_abstract", "max_results": 3})
    if alex.get("records"):
        doi = next((r["document_number"] for r in alex["records"] if r["document_number"].startswith("10.")), "")
        if doi:
            call("literature_fetch", {"doi": doi})
    for source in ("crossref_epmc", "arxiv"):
        call("literature_search", {"query": "skeletal animation", "source": source, "max_results": 1})
    call("gpatents_fetch", {"publication_number": "US9208613B2", "constituent": "claims"})


if __name__ == "__main__":
    main()
