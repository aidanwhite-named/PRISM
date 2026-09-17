# 모델 주도 검색 전환 — 2026-09-17

이후 사용자 요청으로 [탐색 마감·분류 시간 확보](search-deadline-2026-09-17.md)를 추가했다. 아래의 단일 세션·300초 전체 탐색 설명은 최초 전환 시점 기록이며, 현재는 탐색을 최대 195초로 제한하고 필요할 때 보존 자료만으로 마감 분류를 한 번 수행한다.

## 변경

- 기본값과 이 설치의 저장 설정에서 `progressive_search_enabled=false`로 전환했다. 한 CLI 모델 세션이 검색 도구를 선택한다. 과거 단계별 엔진과 실행 기록은 호환용으로 보존했다.
- 웹 검색, EPO/KIPRIS/논문 API를 유지한다. 정해진 소스 순서·단계별 LLM 재호출과 native 경로의 종료 후 `search_followup.run` 강제 보완을 사용하지 않는다.
- `save_candidates`: 정확한 번호/DOI/URL별 후보를 병합·갱신하고 fsync+원자 교체로 저장한다. 저장 자체는 조회 또는 근거 검증으로 인정하지 않는다. 최종 JSON이 없으면 중간 후보를 복구하고 실행 미완료 상태를 유지한다. 최종 JSON이 있으면 모델이 정한 순위·제외를 따른다.
- `citation_search`: 모델이 지정한 문헌의 한 단계 인용/피인용을 조회한다. 특허 후방은 모든 식별자를 반환하며 후속 상세 조회는 모델이 선택한다. 전방은 EPO 페이지를 지정할 수 있다. 패밀리 전체 자동 합산은 하지 않는다.
- `source_fetch`: 공개 HTTPS HTML/PDF를 보존하고 본문·청구항·페이지 구간을 반환한다. `section=page`로 설명·패밀리·인용 표를 읽을 수 있다. 동일 페이지의 추가 구간은 저장한 응답을 재사용한다. 인증서 검증은 계속 켜져 있다.
- 웹 보존 본문의 `generic_json` 근거는 텍스트 대조만 증명한다. 번역 여부가 불명인 Google Patents 페이지를 공식 원문 인용으로 승격하지 않는다. 페이지 번호/URL 식별이 불일치하면 근거를 옮기지 않는다.
- 전체 예산은 300초·80회이며 단계별 시간 배분은 없다. MCP 응답에 남은 시간을 표시한다. 저장한 중간 후보는 실행 중 미리보기와 종료 후 결과에서 확인할 수 있다.

## 인증서 진단

실패했던 동일한 공개 Google grounding 중계 URL에 인증서 검증 ON/OFF를 각각 한 번 적용했다. 두 요청 모두 동일한 `BadStatusLine` 차단 HTML을 받았다.

HTML 내용: **“사내 정책(사용자 정의)에 의해 페이지가 차단되었습니다.”**

따라서 인증서 검증 해제로 해결되지 않았다. 확인된 문구는 사내 정책 차단이며 Google 봇 차단이라는 증거는 아니다. 장비 제품이나 정확한 정책 규칙은 이 시험만으로 특정하지 않았다. TLS 변경은 진단 요청에만 한정했으며 운영 설정은 바꾸지 않았다. 모델 주도 전환은 네트워크 정책을 해제하지 않는다.

- [진단 스크립트](artifacts/joint-search-comparison-20260917/probe_redirect_tls.py)
- [ON/OFF 결과와 한글 해독](artifacts/joint-search-comparison-20260917/redirect-tls-probe.json)

## 실제 검증

### 번호 지정 도구 연결 시험

`WO2012111622A1`의 전방 피인용 조회에서 `JP7475618B1`, `CN113129414A` 두 문헌이 반환되었다. `JP7475618B1`의 직접 페이지에서 청구항 5,741자를 확보했다. **번호를 지정한 진단이며 자동 발견 성공으로 세지 않는다.**

[실행 스크립트](artifacts/joint-search-comparison-20260917/probe_agent_tools.py) · [결과](artifacts/joint-search-comparison-20260917/model-directed-tools-probe.json)

### 청구항만 준 자동 검색

실행 `93129d51-a3f1-44ca-b236-2fce8c81432f`, 기존 설정의 agy 모델, 300초. 검색 입력에 JP/WO 목표 번호를 넣지 않았다.

모델이 웹 검색과 KIPRIS/EPO/논문 API를 선택했고 후보 4개를 저장했다. 300초 시간 초과로 최종 JSON과 분류를 완성하지 못했으나 중간 후보를 복구했다. 후보는 Fast and Easy Reach-Cone Joint Limits, Parametrization and Range of Motion of the Ball-and-Socket Joint, Progressive Clamping, US9786085B2다.

**JP7475618B1 및 WO2012111622A1의 자동 발견은 이 실행에서도 확인되지 않았다.** 모델 자율성이 문헌 발견·종료·분류 품질을 보장하지는 않는다. 이 실행은 도구 선택 전환과 중단 복구의 실증이다. 중간 후보 미리보기와 MCP 남은 시간 표시 코드는 이 실행 이후 보완했고 자동 테스트로 검증한다.

[기존 평가 출력](artifacts/joint-search-comparison-20260917/model-directed-metrics.json)의 known-relevant recall 1.0은 평가 목록에 있는 관련 논문 **한 편**을 찾았다는 뜻이다. 목표 특허나 전체 유사문헌을 모두 찾았다는 뜻이 아니다. 이 출력의 일부 근거·시간 집계는 단계별 엔진용이므로 native 실행의 상세 비교에는 도구 저널과 저장 후보를 사용해야 한다.

## 검증 범위

검색 관련 백엔드 회귀 테스트와 신규 checkpoint/도구/timeout 복구 테스트, 결과 화면 렌더링 테스트를 실행했다. 신규 테스트는 중단 후 재시작 시 후보 병합, 저장을 조회로 오인하지 않음, 번호 불일치 근거 차단, 본문 구간 캐시, 일반 캡처의 원문 등급 오인 방지, 모델 재호출 없이 timeout/잘못된 최종 응답 복구를 다룬다.

- 최초 관련 백엔드 묶음 195개 통과. 이후 미리보기/남은 시간 보완 후 영향 범위 68개 및 최종 신규 도구 테스트 7개 통과(서로 중복되는 묶음이며 합산하지 않음).
- 결과 화면 렌더링 13개와 프런트엔드 production build 통과.
- 실행 중인 작업이 없음을 확인한 뒤 로컬 8765 앱 서버를 재시작했다. `/api/health` 정상, 설정 `progressive_search_enabled=false`, 실제 실행 결과의 `execution_mode=model_directed`와 복구 후보 4개를 API로 재확인했다.
