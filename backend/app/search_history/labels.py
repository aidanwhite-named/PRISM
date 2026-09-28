"""Labels found in historical search records."""
GROUP_DEFINITIONS = {
    "A": "전체 구조와 핵심 특징이 모두 강하게 유사",
    "B": "전체 구조는 다르지만 핵심 특징 또는 핵심 관계가 강하게 유사",
    "C": "전체 구조는 유사하지만 핵심 대응은 부분적",
}

ISSUE_LABELS = {
    "identifier_unverified": "식별 미확인",
    "identifier_invalid": "식별자 형식 오류",
    "identifier_mismatch": "응답 또는 URL의 문헌 식별자 불일치",
    "source_not_read": "페이지 본문 열람 미확인",
    "quote_unverified": "직접 인용 검증 불가",
    "support_unverified": "근거 문장 대조 미확인",
    "duplicate_group_conflict": "동일 문헌에 대한 LLM 그룹 충돌",
    "publication_date_conflict": "공개일 출처 충돌",
    "publication_date_unverified": "공개일 대조 미확인",
    "source_conflict": "출처 간 필드 내용 차이",
    "title_unverified": "명칭 대조 미확인",
    "title_mismatch": "보고 명칭과 보존 원문 명칭 차이",
    "applicant_unverified": "저자·출원인 대조 미확인",
    "applicant_mismatch": "보고 저자·출원인과 보존 원문 차이",
}

LEVEL_LABELS = {
    "search_snippet_only": "검색 스니펫·모델 판단 / 원문 미검증",
    "source_page_reviewed": "페이지 열람 확인 / 원문 인용 미검증",
    "official_bibliographic": "공식 서지 확보",
    "official_abstract": "공식 초록 확보",
    "official_claims": "공식 청구항 확보",
    "official_full_text": "공식 전문 확보",
    "public_capture": "웹 본문 보존·대조 / 원문 언어 미확인",
}

REASON_LABELS = {
    "unsupported_transport": "실행별 도구 연결 미지원", "not_implemented": "접속 미구현",
    "not_registered": "agy MCP 서버 미등록",
    "disabled": "연동 꺼짐", "not_configured": "인증 미설정",
    "limit_exhausted": "호출·쿼터 한도 소진", "access_failed": "조회 실패",
    "timeout": "시간 한도 소진", "rate_limited": "Provider 사용량 제한",
    "cancelled": "취소됨", "outcome_unknown": "호출 완료 여부 미확인",
    "page_read_without_provenance": "페이지 열람 성공 · 보존 근거 대조 경로 없음",
}
