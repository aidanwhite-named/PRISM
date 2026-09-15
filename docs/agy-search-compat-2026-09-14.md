# agy 1.2.2 검색 응답 호환 수정

`search_web`이 `no summary returned from GenerateContent`로 실패하던 문제를
PRISM의 agy 검색 실행 경로에서 보완했다. agy 실행 파일 소스는 제공되지 않아
CLI 자체를 수정하지 않고, 실행 중에만 동작하는 로컬 중계를 사용한다.

## 확인한 원인

같은 회사망에서 agy의 OAuth 요청을 원래 호스트
`daily-cloudcode-pa.googleapis.com`으로 전달해 응답을 확인했다.
검색용 `/v1internal:generateContent`는 HTTP 200과 정상 종료 `STOP`, 실제 요약과
출처를 반환했다. 선택 모델은 `gemini-3.6-flash-low`지만 agy 내부 검색은
`gemini-3.5-flash-lite`와 `googleSearch`를 사용했다.

`response.candidates[0].content.parts`의 앞 두 항목은
`{"thought": true, "text": ""}`였고, 세 번째 항목에는 실제 요약 3,336자가 있었다.
원본 전달은 실패했고, 별도 대조 호출에서 빈 항목 두 개만 제거해 전달하자
`search_web`이 성공했다. 인증·리전 제한 오류나 429는 관측하지 않았다.

`vertexaisearch.cloud.google.com`의 회사 정책 차단은 별도로 남아 있다.
이 도메인은 응답의 출처 리다이렉트 링크에 사용된다. 이번 요약 파싱 오류와는
구분하며, 후속 출처 열람까지 복구됐다고 주장하지 않는다.

## 적용 범위

- CLI가 정확히 `1.2.2`이고 agy 검색 정책으로 실행할 때만 적용한다.
  일반 분석, 로그인, 모델 목록 확인에는 적용하지 않는다.
- 실행마다 임의 포트와 비밀 경로의 loopback 중계를 만들고, 해당 CLI 자식 환경의
  `CLOUD_CODE_URL`에만 지정한다. 전역 환경과 agy 설정 파일은 변경하지 않는다.
- 목적지는 원래 Google 호스트와 확인된 API 경로 목록으로 고정한다.
  브라우저 Origin 요청과 임의 경로는 거절하고 리다이렉트를 따라가지 않는다.
- 인증정보는 CLI가 공급하며 메모리에서만 원래 호스트로 전달한다.
  기본 TLS 인증서 검증을 유지하고 요청/응답 본문이나 토큰은 로그에 쓰지 않는다.
- HTTP 200인 `googleSearch` JSON 응답만 검사한다. 선행 항목이 정확히 빈
  thought/text이고 바로 뒤에 실제 답변 텍스트가 있을 때만 제거한다.
  서명·도구 호출·새 필드가 있거나 유효한 답변이 없으면 원본을 보존한다.
- 실제 답변, 근거, 사용량, 오류 응답은 보존한다. SSE는 즉시 전달하고 추가 모델
  호출이나 검색 재시도를 만들지 않는다. 교정 응답 수와 제거 항목 수만
  `provider_compatibility` 이벤트에 남긴다.
- 실행 종료·예외·취소 시 listener와 활성 연결을 닫는다. 새 버전은 검증 전까지
  이 우회를 자동 적용하지 않는다.

## 검증

교정 함수, 청크 전송, SSE 즉시 전달, 오류/압축/리다이렉트 원본 보존,
임의 프록시 사용 거절, 취소/실행 예외 시 정리, 버전·검색 정책별 적용 범위 및
기존 Provider·검색 API 회귀 테스트 **205개 통과**.

수정된 Provider의 실제 `search_check`에서 `search_web ok=true`, `OK`를 확인했다.
`provider_compatibility`에 응답 1개, 빈 항목 2개 교정이 기록됐다.
진단 자료는 `%LOCALAPPDATA%/PRISM/diagnostics/search-endpoints-20260914/`에 있다.

실행 중인 작업이 없음을 확인하고 8765 서버를 재시작했다. 재시작된 앱의
`POST /api/providers/agy/search-check`도 `OK`로 완료되어 저장된 웹 채널 상태가
`unreachable`에서 `available`로 갱신됐다. 기존 실패 보고서는 변경하지 않았고,
전체 유사문헌 보고서 생성은 재실행하지 않았다.
