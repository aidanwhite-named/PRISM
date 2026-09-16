"""Runtime call accounting and unclassified records retained after interruption."""
from __future__ import annotations

NATIVE_COUNT_FILE = "search_native_calls.json"


def budget_status(used: int, limit: int) -> dict:
    # Leave headroom for calls already submitted in a parallel batch.
    finish_at = max(1, limit - min(4, max(1, limit // 10)))
    return {"used": used, "limit": limit, "remaining": max(0, limit - used),
            "finalize_at": finish_at,
            "action": "finalize_now" if used >= finish_at else "continue",
            "instruction": ("추가 도구 호출을 멈추고 확보한 자료로 최종 JSON을 작성하십시오. "
                            "미확인 항목과 남은 과제를 그대로 남기십시오.") if used >= finish_at else ""}


def prompt(limit: int) -> str:
    threshold = budget_status(0, limit)["finalize_at"]
    return (f"\n\n[이 실행의 호출 예산]\n검색·상세 조회·상태 확인·실패·웹 도구를 합쳐 최대 {limit}회입니다. "
            f"{threshold}회까지 사용하면 추가 호출 없이 최종 JSON을 작성하십시오. "
            "도구 응답의 budget.action이 finalize_now이면 즉시 결과 정리로 전환하십시오. "
            "동시에 보낼 호출도 남은 예산에 포함하고, 미확인 항목은 미확인으로 남기십시오.")


def retained_records(journal: list) -> list[dict]:
    """Preserve fetched metadata, without choosing candidates or inventing groups."""
    from .search_manifest import identity_key
    records = {}
    for row in journal:
        if row.get("state") != "completed" or row.get("ok") is not True or not row.get("tool", "").endswith(("_fetch", "_search")):
            continue
        for record in (row.get("result") or {}).get("records", []):
            number = str(record.get("document_number") or record.get("doc_number") or "")
            doi = str(record.get("doi") or "")
            url = str(record.get("source_url") or record.get("url") or "")
            if not (number or doi or url):
                continue
            if not doi and number.startswith("10."):
                doi, number = number, ""
            key = identity_key(number, doi) if number or doi else url
            item = records.setdefault(key, {"document_number": number, "doi": doi,
                "title": str(record.get("title") or ""), "url": url, "scopes": [], "call_ids": []})
            scope = ("bibliographic_search" if row["tool"].endswith("_search") else
                     (row.get("arguments") or {}).get("constituent", "abstract" if row["tool"] == "literature_fetch" else "claims"))
            if not item["title"]:
                item["title"] = str(record.get("title") or "")
            if scope not in item["scopes"]:
                item["scopes"].append(scope)
            item["call_ids"].append(row.get("id", ""))
    return list(records.values())
