"""KIPRIS Plus freeSearchInfo: domestic patent/utility bibliographic discovery.

API contract: https://plus.kipris.or.kr/portal/popup/DBII_000000000000001/SC002/ADI_0000000000010162/apiDescriptionSearch.do
Search abstracts are never promoted to claims/full-text evidence.
"""
from datetime import datetime, timezone
import re
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlencode

from ..config import PATHS
from . import parsers, kipris_quota
from .artifacts import ArtifactStore
from .epo_parser import parse_xml
from .base import (BackendStatus, EvidenceRef, FieldValue, PatentRecord, PatentSearchBackend,
                   PatentSearchError, PatentSearchNotConfigured, PatentSearchResponse,
                   SOURCE_NORMALIZED, TRANSLATION_UNKNOWN)

ENDPOINT = 'https://plus.kipris.or.kr/openapi/rest/patUtiModInfoSearchSevice/freeSearchInfo'
PROFILE = 'kipris_search_xml'
MAX_BYTES = 8 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _live_transport(params):
    # No retries or redirects: each outbound request must have its own reservation,
    # and the accessKey must only reach the fixed HTTPS endpoint.
    # urllib does not emit HTTP client INFO logs containing accessKey URLs.
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(ENDPOINT + '?' + urlencode(params),
        headers={'User-Agent': 'PRISM-KIPRIS/1.0', 'Accept': 'application/xml'})
    try:
        with opener.open(request, timeout=15) as response:
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise PatentSearchError('키프리스 응답 크기 제한을 초과했습니다.')
            return data
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise PatentSearchError(f'키프리스 HTTP {status}: API 키와 서비스 신청 상태를 확인하세요.') from None


def _tag(node):
    return node.tag.rsplit('}', 1)[-1].lower()


def _fields(node):
    return {_tag(child): ''.join(child.itertext()).strip() for child in node}


def parse(data):
    try:
        root = parse_xml(data)
    except Exception:
        raise PatentSearchError('키프리스 XML 응답을 읽을 수 없습니다.') from None
    tags = {_tag(n): (n.text or '').strip() for n in root.iter()}
    # Fault bodies may arrive with HTTP 200. Never treat them as zero hits.
    for key in ('resultcode', 'returnreasoncode', 'errorcode'):
        if tags.get(key) and tags[key] not in ('0', '00', '000', '0000'):
            raise PatentSearchError('키프리스 API 오류: API 키, 서비스 승인 및 월간 한도를 확인하세요.')
    rows = [_fields(n) for n in root.iter() if _tag(n) in ('patentutilityinfo', 'item')]
    total = tags.get('totalsearchcount', tags.get('totalcount', ''))
    if not total.isdigit():
        raise PatentSearchError('키프리스 검색 응답 형식이 올바르지 않습니다.')
    return rows, int(total)


def document(row):
    opening = re.sub(r'\D', '', row.get('openingnumber', ''))
    registration = re.sub(r'\D', '', row.get('registrationnumber', ''))
    # KIPRIS numbers carry a 10 (patent) / 20 (utility) prefix. Applications
    # alone do not establish a publication and are intentionally not substituted.
    if len(opening) == 13 and opening[:2] in ('10', '20'):
        number = 'KR' + opening[2:] + ('A' if opening.startswith('10') else 'U')
        date = row.get('openingdate', '')
    elif len(registration) in (9, 13) and registration[:2] in ('10', '20'):
        number = 'KR' + registration[:9] + ('B1' if registration.startswith('10') else 'Y1')
        date = row.get('publicdate', '')  # registration date is not a publication date
    else:
        return None
    values = {'title': row.get('inventionname', ''), 'abstract:ko': row.get('abstract', ''),
              'applicants': row.get('applicant', ''),
              'ipc': row.get('internationalpatentclassificationnumber', ''),
              'publication_date': date,
              'application_number': row.get('applicationnumber', '')}
    return number, {k: v for k, v in values.items() if v}


def _extract(data, field_path):
    index, field = field_path.split('/', 1)
    rows, _ = parse(data)
    try:
        return document(rows[int(index)])[1][field]
    except (IndexError, KeyError, TypeError, ValueError):
        raise parsers.FieldPathMissing('키프리스 필드가 없습니다.') from None


parsers.register_parser('kipris_xml', '1', _extract)
parsers.register_profile(parsers.SourceProfile(
    profile_id=PROFILE, parser_id='kipris_xml', parser_version='1',
    source_kind=SOURCE_NORMALIZED, translation_state=TRANSLATION_UNKNOWN,
    language='ko', raw_capable=False, note='KIPRIS search metadata and abstract; not full text.'))


class KiprisBackend(PatentSearchBackend):
    id = 'kipris'
    display_name = '키프리스 (KIPRIS Plus)'

    def __init__(self, *, transport=None, reserve=None):
        self.values = {}
        self.transport = transport or _live_transport
        self.reserve = reserve or kipris_quota.reserve

    def configure(self, values):
        self.values = values

    def status(self):
        enabled = bool(self.values.get('kipris_integration_enabled'))
        configured = enabled and bool(str(self.values.get('kipris_api_key') or '').strip())
        return BackendStatus(self.id, self.display_name, enabled, configured,
                             '국내 특허·실용신안 검색' if configured else 'KIPRIS Plus API 키를 입력하세요.')

    def search(self, query, *, begin=1):
        if not self.status().configured:
            raise PatentSearchNotConfigured('키프리스 연동과 API 키를 설정하세요.')
        if not isinstance(query.text, str) or not query.text.strip() or len(query.text) > 500:
            raise ValueError('키프리스 검색어는 1~500자여야 합니다.')
        if type(query.max_results) is not int or not 1 <= query.max_results <= 100:
            raise ValueError('키프리스 결과 수는 1~100이어야 합니다.')
        if type(begin) is not int or not 1 <= begin <= 1000:
            raise ValueError('키프리스 페이지는 1~1000이어야 합니다.')
        params = {'word': query.text.strip(), 'patent': 'true', 'utility': 'true',
                  'docsStart': begin, 'docsCount': query.max_results}
        key = str(self.values['kipris_api_key']).strip()
        self.reserve()
        try:
            data = self.transport({**params, 'accessKey': key})
        except PatentSearchError:
            raise
        except Exception:
            # HTTP exceptions contain the request URL (and accessKey).
            raise PatentSearchError('키프리스 연결에 실패했습니다. 네트워크 상태를 확인하세요.') from None
        rows, total = parse(data)
        if key.encode() in data:
            raise PatentSearchError('키프리스 응답에 인증 정보가 포함되어 저장하지 않았습니다.')
        aid = ArtifactStore(PATHS.evidence_dir).put(data)
        records = []
        for index, row in enumerate(rows[:query.max_results]):
            parsed = document(row)
            if parsed is None:
                continue
            number, values = parsed
            fields = {name: FieldValue(value, EvidenceRef(aid, f'{index}/{name}', PROFILE))
                      for name, value in values.items()}
            records.append(PatentRecord(number, values.get('title', ''), fields,
                                        'https://patents.google.com/patent/' + number))
        return PatentSearchResponse(tuple(records), total, aid,
            datetime.now(timezone.utc).isoformat(), 200, ENDPOINT + '?' + urlencode(params),
            notes=('키프리스 서지·초록 검색 결과입니다. 청구항·전문 확인은 별도입니다.',))
