"""Experimental Kiwee gateway search with preserved, unverified vendor fields."""
from datetime import datetime, timezone
import re
from urllib.parse import quote

from . import parsers
from .artifacts import ArtifactStore
from .base import (BackendStatus, EvidenceRef, FieldValue, PatentRecord, PatentSearchBackend,
                   PatentSearchNotConfigured, PatentSearchResponse)
from .kiwee_client import (ENDPOINT, KiweeError, build_query, live_transport, parse_response,
                           validate_endpoint, validate_shards, validate_thumbprint)
from ..config import PATHS

PROFILE = 'kiwee_solr_json_v1'
ALIASES = {'title': ('tl', 'title'), 'abstract': ('ab', 'abstract'),
           'claims': ('cl', 'claims'), 'publication_date': ('pd', 'publication_date'),
           'ipc': ('ipc',), 'cpc': ('cpc',), 'applicants': ('ap',),
           'kiwee_id': ('id_kipi', 'id'), 'kiwee_publication_number': ('pn_s', 'pn_o')}


def fields_for(row):
    fields = {}
    for name, keys in ALIASES.items():
        for key in keys:
            value = row.get(key)
            if isinstance(value, list):
                value = '\n'.join(str(item) for item in value if isinstance(item, (str, int)))
            if isinstance(value, (str, int)) and str(value).strip():
                fields[name] = str(value)
                break
    return fields


def document_number(row):
    # Kiwee's internal id is not a publication number. Never invent a kind code.
    for key in ('pn_s', 'pn_o'):
        value = row.get(key)
        if not isinstance(value, str):
            continue
        value = re.sub(r'[\s.\-/]', '', value).upper()
        if re.fullmatch(r'[A-Z]{2}\d+[A-Z]\d?', value):
            return value
        pc, kc = row.get('pc', ''), row.get('kc', '')
        combined = str(pc).upper() + value + str(kc).upper()
        if re.fullmatch(r'[A-Z]{2}\d+[A-Z]\d?', combined):
            return combined
    return ''


def _extract(data, path):
    index, field = path.split('/', 1)
    rows, _ = parse_response(data)
    try:
        return fields_for(rows[int(index)])[field]
    except (IndexError, KeyError, ValueError):
        raise parsers.FieldPathMissing('Kiwee 응답에 해당 필드가 없습니다.') from None


parsers.register_parser('kiwee_solr', '1', _extract)
parsers.register_profile(parsers.SourceProfile(PROFILE, 'kiwee_solr', '1', raw_capable=False,
    translation_state='unknown',
    note='Kiwee vendor search response; language and translation status unverified.'))


class KiweeBackend(PatentSearchBackend):
    id = 'kiwee'
    display_name = 'Kiwee 검색 (시험 연동)'

    def __init__(self, *, transport=None):
        self.values = {}
        self.transport = transport or live_transport

    def configure(self, values):
        self.values = values

    def status(self):
        enabled = self.values.get('kiwee_integration_enabled') is True
        try:
            validate_endpoint(self.values.get('kiwee_endpoint', ENDPOINT))
            validate_shards(self.values.get('kiwee_shards', 'shd_kr'))
            validate_thumbprint(self.values.get('kiwee_certificate_thumbprint'))
        except ValueError as exc:
            return BackendStatus(self.id, self.display_name, enabled, False, str(exc))
        return BackendStatus(self.id, self.display_name, enabled, enabled,
            '연결을 시도할 수 있습니다. 서버 인증·검색 권한은 시험 검색으로 확인하세요.')

    def search(self, query, *, begin=1, query_mode='keywords'):
        if not self.status().configured:
            raise PatentSearchNotConfigured('환경 설정에서 Kiwee 연동과 검색 서버를 설정하세요.')
        if type(query.max_results) is not int or not 1 <= query.max_results <= 50:
            raise ValueError('Kiwee 결과 수는 1~50이어야 합니다.')
        if type(begin) is not int or not 1 <= begin <= 10000:
            raise ValueError('Kiwee 페이지는 1~10,000이어야 합니다.')
        q = build_query(query.text, query_mode)
        endpoint = validate_endpoint(self.values.get('kiwee_endpoint', ENDPOINT))
        shards = validate_shards(self.values.get('kiwee_shards', 'shd_kr'))
        params = {'q': q, 'wt': 'json', 'defType': 'lucene',
                  'start': (begin - 1) * query.max_results, 'rows': query.max_results}
        if shards:
            params['shards'] = shards
        status, data = self.transport(endpoint, params, self.values)
        if status != 200:
            from .kiwee_client import _http_error
            raise _http_error(status)
        rows, total = parse_response(data)
        if len(rows) > query.max_results:
            raise KiweeError('KIWEE.RESPONSE', '서버가 요청한 결과 수를 초과해 반환했습니다.', status)
        aid = ArtifactStore(PATHS.evidence_dir).put(data)
        records = []
        unidentified = 0
        for index, row in enumerate(rows):
            number = document_number(row)
            values = fields_for(row)
            if not number:
                unidentified += 1
                continue
            fields = {name: FieldValue(value, EvidenceRef(aid, f'{index}/{name}', PROFILE))
                      for name, value in values.items()}
            records.append(PatentRecord(number, values.get('title', ''), fields,
                'https://patents.google.com/patent/' + quote(number)))
        notes = ['Kiwee 시험 연동입니다. 응답 필드의 원문성·번역 여부는 미확인입니다. 반환되지 않은 청구항·전문은 별도로 확보해야 합니다.']
        if unidentified:
            notes.append(f'공개번호를 확정할 수 없는 {unidentified}건은 후보에서 제외했습니다. 시험 검색의 반환 필드에서 번호 형식을 확인하세요.')
        return PatentSearchResponse(tuple(records), total, aid, datetime.now(timezone.utc).isoformat(),
            status, endpoint, notes=tuple(notes), source_stats=({'source': 'kiwee',
                'returned_records': len(rows), 'total_results': total,
                'unidentified_records': unidentified},))
