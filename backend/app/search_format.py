"""Safe link and text formatting shared by current and saved reports."""
from urllib.parse import quote
from .search_manifest import is_linkable_url
def _link(raw) -> str:
    if not is_linkable_url(raw):
        return "링크 미확인"
    return "[문헌 보기](" + quote(str(raw), safe=":/?&=%#@+;,~.-_") + ")"
