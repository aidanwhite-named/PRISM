"""User-triggered Kiwee trial search. Shares the production search adapter."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictInt
from typing import Literal
from sqlalchemy.orm import Session

from .. import patent_search, settings_service
from ..config import PATHS
from ..db import get_db
from ..patent_search.artifacts import ArtifactStore
from ..patent_search.kiwee_backend import fields_for, document_number
from ..patent_search.kiwee_client import KiweeError, build_query, parse_response

router = APIRouter(prefix='/api/kiwee', tags=['kiwee'])


class TrialSearch(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    query_mode: Literal['keywords', 'solr'] = 'keywords'
    max_results: StrictInt = Field(default=5, ge=1, le=50)
    begin: StrictInt = Field(default=1, ge=1, le=10000)


@router.post('/search')
def search(payload: TrialSearch, session: Session = Depends(get_db)):
    values = settings_service.get_all(session)
    backend = patent_search.get_backend(values, 'kiwee')
    if backend is None or not backend.status().configured:
        raise HTTPException(400, 'Kiwee 연동을 켜고 연결 설정을 저장하세요.')
    try:
        q = build_query(payload.query, payload.query_mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    try:
        response = backend.search(patent_search.PatentSearchQuery(payload.query, payload.max_results),
            begin=payload.begin, query_mode=payload.query_mode)
    except KiweeError as exc:
        return {'ok': False, 'detail': str(exc), 'error_code': exc.fault_code,
                'http_status': exc.http_status, 'solr_query': q, 'records': [],
                'total_found': None, 'returned': 0, 'returned_fields': [], 'notes': [],
                'next_page': None}
    rows, total = parse_response(ArtifactStore(PATHS.evidence_dir).read(response.raw_artifact_id))
    records = [{'document_number': document_number(row), 'fields': fields_for(row)} for row in rows]
    count = len(rows)
    return {'ok': True, 'detail': f'검색 응답을 받았습니다. 전체 {total:,}건 중 {count}건을 가져왔습니다.',
            'error_code': None, 'http_status': response.http_status, 'solr_query': q,
            'records': records, 'total_found': total, 'returned': count,
            'returned_fields': sorted({key for row in rows for key in row}),
            'notes': list(response.notes), 'raw_artifact_id': response.raw_artifact_id,
            'next_page': payload.begin + 1 if payload.begin * payload.max_results < total else None}
