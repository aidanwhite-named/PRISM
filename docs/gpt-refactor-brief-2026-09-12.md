# GPT 설계 요청 초안 (ARIA/PRISM 기존 코드베이스 기준)

---

## 0. 역할

너는 이 프로젝트의 AI 시스템 아키텍트이자 개발 파트너다.
**그린필드 설계를 요청하는 것이 아니다.** 이미 동작하는 코드베이스가 있고, 그 위에
"판단 축적 레이어"를 얹는 설계를 원한다. 기존 자산 중 **재사용할 것 / 감쌀 것 /
버릴 것**을 반드시 명시해라. 기존 것을 버리자고 제안해도 좋지만, 그때는 버리는
이유와 대체 비용을 함께 적어라.

먼저 코드를 작성하지 마라. 설계부터다.

---

## 1. 최종 목표

특허 선행기술 조사 및 선행기술 보고서 작성 업무의 자동화. 다만 목표는 "LLM이 검색해서
보고서를 써 주는 것"이 아니다.

**내가 지금까지 수행해 온 선행기술 조사·보고서 작성 과정에서의 판단 기준을 시스템에
축적하여, 새로운 사건에서도 나의 판단과 최대한 유사한 검색·분석·보고서 결과를 내는
시스템**을 만드는 것이다.

즉 이 시스템의 핵심 가치는 파이프라인이 아니라 **판단의 축적**에 있다.

---

## 2. 현재 코드베이스 (실측)

- Python / FastAPI / SQLAlchemy(SQLite, WAL) / React + Vite + TypeScript
- 백엔드 애플리케이션 코드 약 29,000줄, 단일 사용자 로컬 실행형(1인 사용)

### 2.1 이미 구현되어 있는 것 — 다시 만들 필요 없음

**(a) LLM Gateway / 모델 교체 계층** (`app/providers/`, 약 3,500줄)
- `base.py` / `registry.py` / `resolver.py` + Claude CLI, Codex CLI, Gemini(agy) CLI 어댑터
- 스트리밍 파서, 로그인 흐름, 모델별 입력 한계(`model_limits.py`), 도구 권한 처리
- 프로바이더는 id로 교체 가능. 실행마다 어떤 프로바이더/모델이었는지 기록됨

**(b) 특허 검색 채널** (`app/patent_search/`)
- EPO OPS 클라이언트 / 파서 / 쿼터 관리 / 보존 정책
- 비특허문헌(서지 API) 채널 별도 구현
- 알려진 제약: Google Patents는 차단, EPO 공식 PDF는 열림. 검색 각주는 풀 수 없음

**(c) 근거 기반 검색·추출 파이프라인** (`app/retrieval/`)
- 로컬 인덱싱(`index.py`), 임베딩/시맨틱 검색(`semantic.py`, `embedding_cache.py`)
- 문헌 페이지 분해(`pages.py`), 근거 추출(`extraction.py`), 근거 패키지 구성(`evidence.py`)
- 증거 압축(evidence compaction) 및 소스 풀 관리
- 에이전트 루프(`agent.py`, 2,590줄) — **여기가 유일하게 실제 리팩토링이 필요한 덩어리**

**(d) 감사 기록(manifest) 문화** — 이 프로젝트에서 가장 중요한 기존 자산

`execution_jobs` 테이블에 실행마다 다음이 JSON으로 남는다.
- `retrieval_manifest` — 인덱스 재현 정보, 라운드별 LLM 입출력 해시, 실행된 검색어, 예산, 라이브러리 버전
- `search_manifest` — 검색어, 라운드, 후보 출처, 접근 실패, 검색 프롬프트 해시
- `delivery_manifest` — 문헌을 모델에게 어떻게 전달했는지(폭·사유·실제 크기·한도)
- `analysis_manifest` — 구성별 유사도·미발견·판독 제한을 담은 **기계 판독용** 분석 결과
- `prompt_snapshot`, `prompt_capabilities` — 실행 시점 프롬프트 원문과 선언된 계약
- 각 manifest에 대응하는 `*_error` 칸 — 못 만들었으면 왜 못 만들었는지가 남는다

즉 **"모든 실행은 재현 가능해야 하고, 실패도 사유와 함께 기록된다"** 는 원칙이 이미
코드 전반에 관철되어 있다. 새 설계는 이 원칙을 깨지 말고 확장해야 한다.

**(e) 증거 아티팩트 저장소** (`evidence_references`)
- 특허 원문은 SHA-256 내용 주소로 한 벌만 저장, 여러 작업이 참조
- 아무도 참조하지 않을 때만 삭제 — "이력 전체 삭제"가 실제로 지켜지도록 설계됨

**(f) 프롬프트 저장소** (`app/prompt_store.py`, `prompt_assembly.py`)
- `prompt/` 디렉터리의 마크다운 파일이 유일한 원본. 파일 헤더에 JSON 메타데이터
- 종류(kind)는 현재 **두 가지뿐**: `analysis`(구성대비 분석), `search`(검색 전략)
- 실행 시 원문을 job에 스냅샷하므로 과거 실행 재현은 가능
- **없는 것: 버전 이력, 역할별 분리, 성능 기반 승격 절차**

**(g) 실행 판정 모듈** (`app/evaluation/evaluator.py`)
- 주의: 이것은 **보고서 품질 벤치마크가 아니다.** 도구 정책 위반, 검색 권한 거부,
  출력 절단 탐지 등 **실행이 규칙대로 돌았는지**를 판정한다
- 품질 평가 체계는 사실상 없다고 봐야 한다 (`data/validation/`에 수동 재현 스크립트 몇 개)

**(h) 프론트엔드** (`frontend/src/`)
- 결과 뷰, 검색/검색근거 manifest 뷰, 전달 요약, 구성대비 개요, 미대응 구성 검색 패널
- 즉 **"실행을 보여주는" 화면**은 있고, **"판단을 편집하는" 화면**은 없다

### 2.2 완전히 비어 있는 것 — 설계의 실제 대상

DB 테이블이 7개뿐이고 전부 **실행(job) 중심**이다:
`execution_jobs`, `execution_events`, `attachments`, `provider_snapshots`,
`result_artifacts`, `evidence_references`, `app_settings`.

**판단을 저장하는 테이블이 단 하나도 없다.** 아래는 전부 없다:

- Case / Invention / Claim / **ClaimElement (C1, C2, C3…)**
- **ElementMapping** (element × prior art, 상태 + 근거 passage + 판단이유)
- **Judgment** (신규성/진보성, AI 판단과 사용자 판단의 분리 보존)
- **SearchCandidate**의 채택·제외 기록과 사용자 수정 사유
- **Feedback / Disagreement** (오류 유형 태깅)
- **UserRule**
- **Case 라이브러리** (과거 정답 보고서의 구조화 변환본)

현재 구조에서 청구항은 `execution_jobs.claim_text` 라는 **통짜 텍스트 한 칸**이다.
구성요소 분해 결과는 `analysis_manifest` JSON 안에 실행 부산물로만 존재하고,
독립된 엔티티가 아니며 사용자가 편집할 수 없고 실행 간에 재사용되지 않는다.

**이것이 이 설계 요청의 핵심이다. 파이프라인은 이미 있다. 축적 레이어가 통째로 없다.**

---

## 3. 내가 만들고 싶은 구조

(아래는 내 초안이다. 비판적으로 검토하고, 불필요하게 복잡한 부분은 과감히 지적해라.)

### 3.1 검색 단계

대상 발명의 청구항/명세서 분석 → 청구항을 기술적 구성요소로 분해 → 검색식·검색 전략
생성 → 선행기술 후보 검색 → 각 후보에 대해 **왜 중요한 문헌인지 AI가 판단이유를 생성**
→ 사용자가 후보를 채택/제외하고 그 판단을 수정.

저장해야 할 최소 항목: AI의 검색 판단 / AI의 판단이유 / 사용자의 최종 판단 /
사용자가 수정한 이유 / 관련 claim element / 관련 선행문헌 / 관련 passage.

### 3.2 Claim Element Mapping 단계

각 청구항을 C1, C2, C3… 원자적 구성요소로 나누고 선행문헌과 element-by-element로 대응.

```
C1 → D1 [0032] : Explicit
C2 → D1 [0041] : Explicit
C3 → D1 [0045] : Partial
C4 → 없음      : Not disclosed
```

상태값:

- `E` Explicit disclosure
- `I` Inherent disclosure
- `P` Partial
- `N` Not disclosed
- `U` Uncertain

AI는 **모든 mapping에 대해 근거 passage와 판단이유를 제시**해야 한다.
사용자는 mapping과 판단이유를 수정할 수 있어야 하고, **AI 원본과 사용자 수정본을
모두 저장**한다.

### 3.3 신규성/진보성 판단 단계

Element Mapping을 기반으로 AI가 신규성 및 필요시 진보성 판단.
저장: AI 판단 / AI 판단이유 / 근거 / 사용자 판단 / 사용자 판단이유 /
**AI와 사용자의 disagreement**.

### 3.4 보고서 생성 단계

확정된 검색결과·mapping·판단을 바탕으로 보고서 생성.
AI 작성본과 사용자 최종 수정본을 모두 저장.

**중요:** 검색 단계의 판단이유 / element mapping의 판단이유 / 최종 법적·기술적
판단이유 / 보고서 작성상의 표현 수정을 **서로 다른 데이터로 관리**해야 한다.
이 네 가지는 학습 신호의 성격이 완전히 다르다.

### 3.5 Feedback 시스템

AI 판단을 수정할 때 최종 텍스트만 저장하지 말고 구조화해서 저장:
AI가 무엇이라고 판단했는지 / 내가 무엇으로 바꿨는지 / 어느 부분을 바꿨는지 /
왜 바꿨는지 / 어떤 오류 유형인지 / 관련 claim element / 관련 prior art / 관련 passage.

오류 유형 예시: 구성요소 누락 / 기능 차이 / 구성요소 간 관계 차이 / 실시예 혼합 /
용어를 지나치게 넓게 해석 / 용어를 지나치게 좁게 해석 / implicit disclosure 오판 /
passage 오인 / 검색 relevance 오판 / 날짜·우선권 문제 / 기타.

매번 긴 글을 쓰지 않아도 되도록 **오류 유형 선택 + 짧은 자유입력** 방식을 고려 중.

### 3.6 User Rule 시스템

반복되는 수정에서 판단 기준을 일반화하고 싶다.

예: AI가 반복적으로 "A와 B가 각각 존재하므로 A에 따라 B를 제어하는 관계도 존재한다"
고 잘못 판단하고 내가 이를 수정한다면, 시스템이 다음과 같은 Rule 후보를 생성:

> "두 구성요소가 각각 개시되어 있는 것과 두 구성요소 사이의 특정 기능적/인과적
> 관계가 개시되어 있는 것은 구별해야 한다. 관계 자체에 대한 명시적 또는 필연적
> 근거가 필요하다."

사용자가 이 Rule을 **채택 / 수정 / 폐기**할 수 있고, 채택된 Rule은 User Rule DB에
저장되어 이후 동일·유사 판단에 활용된다.

### 3.7 Prompt 개선 시스템

수정 하나마다 자동으로 prompt를 바꾸는 방식은 **원하지 않는다.** 대신:

```
사용자 수정 → Feedback DB 저장 → 반복 오류 패턴 분석 → Rule 후보 생성
→ Prompt 개선안 생성 → 기존 benchmark dataset으로 평가
→ 성능이 향상된 경우에만 새 prompt version 적용
```

Prompt는 하나의 거대한 것이 아니라 역할별로 분리하고 싶다:
claim decomposition / search query generation / search relevance /
element mapping / novelty judgment / inventive-step judgment / report generation.

(현재는 `analysis` 통짜 프롬프트 1개 + `search` 전략 프롬프트 1개 구조다.
7개로 쪼개는 것이 정말 옳은지, 아니면 과설계인지도 함께 판단해 달라.)

### 3.8 기존 정답 데이터 활용

내가 지금까지 작성한 정답 보고서와 실제 사용한 선행기술 자료를 제공할 수 있다.
단순히 문체를 따라 하는 것이 아니라 **그 데이터에서 실제 판단 기준을 추출**하고 싶다.

과거 사건을 다음 구조로 변환할 생각이다:
Case = Invention / Claims / Claim Elements / Search Strategy / Search Candidates /
Selected Prior Art / Rejected Prior Art / Element Mapping / Evidence Passage /
Final Judgment / Judgment Reason / Final Report.

이 데이터를 RAG, few-shot example, User Rule 추출, benchmark에 활용하고 싶다.

---

## 4. 설계 원칙 (타협 불가)

- LLM의 결론보다 evidence를 우선한다.
- AI의 원본 판단을 덮어쓰지 않는다.
- AI 판단과 사용자 최종 판단을 모두 보존한다.
- 모든 중요한 판단은 관련 문헌과 passage까지 추적 가능해야 한다.
- 사용자의 수정 행동 자체를 학습 데이터로 만든다.
- 사용자별 판단 기준을 명시적인 Rule로 축적한다.
- prompt는 버전 관리한다.
- 모델 역시 버전 및 호출 결과를 기록한다.
- 새 prompt/model 적용 전에 기존 benchmark로 regression test 한다.
- 시스템이 스스로 prompt를 무제한 변경하도록 하지 않는다.
- **최종 판단권은 사용자에게 있다.**

---

## 5. 제약 조건 (설계에 반드시 반영)

1. **1인 사용, 로컬 실행.** 멀티테넌시·인증·RBAC·수평 확장·Kubernetes는 필요 없다.
   `user_id` 컬럼조차 지금은 과설계일 수 있다 — 필요하다면 왜 필요한지 근거를 대라.
2. **현재 SQLite, Alembic 없음.** 스키마 변경은 `_add_compatible_columns()` 라는
   수동 ALTER 헬퍼로 하고 있다. 판단 레이어는 관계가 복잡해서 이 방식으로 못 버틴다.
   PostgreSQL로 옮길지, SQLite를 유지하고 Alembic만 도입할지 **명확히 권고**하고
   그 근거와 마이그레이션 비용을 적어라.
3. **Vector DB 도입 여부를 신중히 판단하라.** 이미 로컬 임베딩 + 캐시 + 시맨틱 검색이
   동작 중이다. 전용 Vector DB(pgvector/Qdrant 등)를 새로 넣어야 하는지, 아니면
   기존 것으로 충분한지 답하라. 나는 스택이 늘어나는 것을 경계한다.
4. **LLM 호출은 CLI 프로바이더 경유.** 각 CLI의 도구 권한·스트리밍·입력 한계 제약이
   이미 코드에 반영되어 있다. 단계별로 다른 모델을 배치하는 설계는 이 계층을 전제로 하라.
5. **외부 접근 제약 실측치:** Google Patents 차단, EPO 공식 PDF 접근 가능, 외부 HTTPS는
   조직 프록시에서 TLS 재서명됨. 검색 각주는 풀 수 없다.
6. **기존 manifest/스냅샷 원칙을 깨지 마라.** 새 엔티티도 "실행 시점 값을 스냅샷하고,
   실패하면 사유를 남긴다"는 기존 패턴을 따라야 한다.
7. **점진적 도입이 가능해야 한다.** 기존 실행 이력과 동작 중인 파이프라인을 죽이지 않고
   단계적으로 붙일 수 있는 경로를 제시하라. 빅뱅 재작성은 받아들이지 않는다.

---

## 6. 답해야 할 질문

### 우선순위 A — 이번 답변에서 반드시 깊게 다룰 것

1. **현재 구조에서 빠진 핵심 기능이나 데이터가 무엇인가?** (2.2 목록 외에 더 있는가)
2. 검색 판단이유 / element mapping 판단이유 / 신규성·진보성 판단이유 / 보고서 표현
   수정 데이터를 **어떻게 분리해서 저장해야 하는가?**
3. AI와 사용자의 disagreement를 **가장 가치 있는 학습 데이터로 만들려면 DB schema를
   어떻게 설계해야 하는가?**
4. 전체 DB schema를 제안하라. 주요 table, 컬럼, relation, 인덱스까지.
   **기존 7개 테이블과 어떻게 연결되는지**를 반드시 명시하라
   (특히 `execution_jobs`, `evidence_references`, 그리고 `analysis_manifest` JSON에
   들어 있는 구성요소 정보를 정식 테이블로 승격할 때의 마이그레이션 경로).
5. Feedback UI를 어떻게 설계해야 **업무를 방해하지 않으면서** 충분한 학습 데이터를
   얻는가? (나는 실제 업무 중에 이걸 쓴다. 입력 비용이 크면 시스템 전체가 죽는다)
6. 웹 UI 화면 설계 — 특히 **검색결과 검토 / element mapping 수정 / AI 판단 수정 /
   User Rule 관리** 4개 화면. 기존 React 컴포넌트 구조 위에 어떻게 얹을지 포함.
7. **가장 먼저 구현해야 할 5가지** — 기존 코드베이스 기준으로, 파일 단위까지 구체적으로.
8. **내가 구현 전에 결정해야 할 질문 목록.**

### 우선순위 B — 방향과 근거만 간결하게

9. 사용자 수정에서 User Rule을 자동 추출하는 구조.
10. User Rule / 과거 사례 RAG / system prompt 각각의 역할 분담.
    (셋이 충돌할 때 우선순위는?)
11. Prompt 자동 개선 시스템에서 prompt drift와 성능 저하를 막는 방법.
12. 기존 정답 보고서를 어떤 schema로 변환해야 가장 가치 있는 학습 데이터가 되는가.
    (변환 자체를 LLM에게 시킬 것인가? 그러면 변환 오류는 어떻게 잡는가?)
13. evidence-grounded architecture — LLM이 근거 없는 판단을 못 하게 만드는 구조적 장치.
14. **어떤 부분을 deterministic code로 처리하고 어떤 부분만 LLM에게 맡길 것인가.**
15. 단계별 benchmark와 evaluation metric.
16. 각 단계(검색 / 후보선정 / element mapping / 판단 / 보고서)에 어떤 LLM을 배치할지.
    모델은 교체 가능해야 한다.
17. MVP → V2 → V3 로드맵.

### 우선순위 C — 짧게

18. 내 접근 중 **불필요하게 복잡한 부분**을 과감히 지적하고 더 단순한 대안을 제시하라.
    특히 3.7(prompt 7분할)과 3.8(과거 사건 전체 구조화)이 과설계가 아닌지 검토하라.
19. 특허 선행기술 조사라는 업무 특성상 **일반적인 RAG/AI 서비스와 다르게 고려해야
    하는 문제**를 찾아라. (예: 우선일·공개일 판정, 인용 적격성, claim construction의
    법적 성격, 부정적 증거(not disclosed)의 증명 불가능성, 번역 문헌 처리, 같은
    패밀리 문헌의 중복 처리 등)

### 답하지 않아도 되는 것

- 전체 기술 스택 추천 (이미 확정: Python/FastAPI/SQLAlchemy/React+Vite+TS)
- 인증·권한·배포·CI/CD·컨테이너화
- LLM 프로바이더 추상화 계층 설계 (이미 구현됨)
- 특허 검색 API 연동 방식 (이미 구현됨)

---

## 7. 답변 형식

다음 순서로 작성하라.

- **A. 현재 아이디어의 문제점 및 개선점** — 비판부터. 동의하는 부분은 짧게.
- **B. 기존 자산 판정** — 재사용 / 감쌀 것 / 버릴 것 표
- **C. Data Model / DB Schema** — 기존 테이블과의 연결 포함. 여기에 가장 많은 분량을 써라.
- **D. Feedback & Disagreement 구조**
- **E. User Rule 시스템**
- **F. Prompt Management 구조**
- **G. RAG / 기존 보고서 활용 방법**
- **H. UI/UX 설계**
- **I. Evaluation / Benchmark 설계**
- **J. MVP → V2 → V3 로드맵**
- **K. 가장 먼저 구현해야 할 5가지** — 파일 단위
- **L. 구현 전에 내가 결정해야 할 질문**

각 항목에서 **권고는 하나로 좁혀라.** 선택지를 나열하고 "상황에 따라 다르다"로
끝내지 마라. 트레이드오프는 권고 뒤에 한 문단으로 붙여라.
