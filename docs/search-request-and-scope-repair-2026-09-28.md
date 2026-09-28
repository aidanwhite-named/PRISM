# EPO 요청 형식과 문헌 확보 범위 표시 수정

## 원인

- EPO OPS는 한 페이지 최대 100건을 허용하지만 도구와 HTTP 클라이언트는 20건으로 제한되어 25건 요청을 거절했다.
- 검색식 스키마가 설명문뿐이어서 `op/items` 그룹의 `type` 누락을 예방하지 못했다. 파서는 이를 단일 검색항으로 간주해 `unknown_cql_term_field`를 반환했다.
- 문헌 조회 응답의 `verification_scope`가 실제 필드가 아닌 요청 종류였다. 초록을 요청해도 서지만 반환될 수 있다.
- 모델이 후보 저장 후 초록을 조회하면 카드의 `reported_scope`는 갱신되지 않을 수 있었다.

## 변경

- EPO 페이지 상한을 공식 한도인 100건으로 통일. 도구 기본 요청량 10건 및 하위 계층 기본 20건은 유지. `coverage.next_begin`으로 필요한 후속 페이지를 선택하며 2,000건 접근 경계를 지킨다. 상한 초과 오류에는 페이지 요청 방법을 반환한다. 모든 페이지를 자동으로 읽거나 검색어를 바꾸지 않는다.
- term/group/date_range의 필드·연산자·하위 구조를 기계 판독 가능한 스키마에 정의. type이 없더라도 구조가 유일하게 결정되는 경우만 추론하여 `query_normalizations`에 남긴다. 혼합 구조, 잘못된 명시적 type, 연산자는 거절한다. 실제 CQL을 기존대로 반환한다.
- 자료 범위는 실제 반환된 비어 있지 않은 필드로 계산. 요청 범위는 `requested_scope`로 별도 보존한다. DOI 및 URL 표기를 정규화해 후속 조회를 같은 문헌에 연결하며 실패·식별자 불일치 응답은 제외한다.
- 화면과 보고서의 주요 자료 표시는 `프로그램이 확보한 자료`. 모델이 작성한 확인 범위는 별도 과거 진술로 보존한다. 자료 수신은 모델의 독해나 구성 대응 검증을 보증하지 않는다.
- 저장된 자율 검색도 조회 시 출처 기록으로 표시를 재계산한다. 기존 저장 파일과 모델 원문은 변경하지 않는다. 모델에는 추가 조회 후 후보의 확인 범위도 다시 저장하도록 지시한다.

## 확인

- 실패 요청의 25건 및 type 누락 재현, 100건 페이지, 2,000건 경계, 중첩 AND/OR/NOT 의미 유지, 잘못된 구조 거절 검사.
- 초록 없는 응답, 늦게 확보된 초록, DOI 표기 차이, 실패·불일치 출처, 과거 결과의 읽기 시 표시 갱신 검사.
- 관련 백엔드 검사, 화면 표시 검사 및 프론트엔드 빌드.
- 실제 EPO 요청: `(ta all "spectral subtraction" and ta all "adaptive noise estimation")`, `max_results=25`, 루트 type 생략 → 정상 응답 18건. 검증 기록은 `data/validation/epo-request-repair-20260928`에 저장. 사용자 검색 이력은 새로 생성하지 않았다.

공식 한도: https://www.epo.org/en/service-support/faq/searching-patents/open-patent-services/general-information/how-many-results-are
