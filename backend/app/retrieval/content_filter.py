"""Conservative exclusion of bibliography-only chunks from technical retrieval.

Source text is never deleted. Identity extraction and explicit page reads still
use the complete index. Mixed first pages with abstracts/claims/body are kept.
"""
import re

_TECHNICAL = re.compile(
    r"[\[【]\s*\d{3,5}\s*[\]】]|청구\s*항\s*\d|청구\s*범위|"
    r"(?:발명의|발명을)\s*(?:내용|상세|실시)|기술\s*분야|배경\s*기술|"
    r"요\s*약|abstract|what\s+is\s+claimed|detailed\s+description|"
    r"^\s*\d+\.\s+(?:A|An|The)\s+(?:method|system|apparatus|device)", re.I | re.M)
_BIB = re.compile(
    r"\((?:19|11|12|21|22|30|43|45|51|52|56|71|72|73|74)\)\s*"
    r"(?:CPC|IPC|국제|특허|출원|공개|등록|발명자|대리인|우선권|선행|대한민국|"
    r"Inventor|Applicant|Assignee|Appl|Filed|Date|Pub|United|References)|"
    r"^\s*(?:Inventors?|Applicants?|Assignees?|References Cited|"
    r"U\.S\. PATENT DOCUMENTS|FOREIGN PATENT DOCUMENTS)\s*[:\n]", re.I | re.M)


def bibliography_only(row) -> bool:
    text = row.text or ""
    section = str(row.section or "").upper()
    if _TECHNICAL.search(text) or section in {"DESCRIPTION", "CLAIMS", "ABSTRACT", "청구범위", "요약"}:
        return False
    marker = _BIB.search(text)
    if marker is None:
        return False
    # A continued abstract can precede the inventor block on page 2, without
    # repeating its heading. Preserve substantive text before the first marker.
    if len(re.sub(r"\s", "", text[:marker.start()])) > 100:
        return False
    return True
