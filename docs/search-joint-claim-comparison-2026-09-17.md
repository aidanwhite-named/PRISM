# 골격 모델 청구항: 직접 검색과 PRISM 실제 검색 비교

실험일: 2026-09-17. 입력은 사용자가 제시한 청구항 1 전체이며, 검색 기준일과 명세서는 주어지지 않았다.
프로그램 검색은 현재 작업 트리의 구현과 저장된 기본 설정을 사용했다. 검색 알고리즘이나 사용자 설정은 이번 비교에서 변경하지 않았다.

**이번 사례에서는 직접 검색이 최종 결과의 관련성과 원문 확인 면에서 더 좋았다.** 프로그램은 120초·300초 실행 모두 관련성 높은 문헌을 결과에 남기지 못했다. 그러나 같은 API에 직접 탐색에서 얻은 용어를 넣으면 핵심 논문이 반환됐다. 검색 채널을 바꾸거나 시간을 늘리는 것만으로 해결할 문제가 아니라, 질의 적응·후보 보존·원문 검토 전환을 함께 개선해야 한다.

## 비교 조건

- 직접 검색: 이 대화의 웹 검색 도구로 후보를 찾고 논문·특허 본문을 열어 확인했다. 최초 검색에는 정답 번호·논문 제목·출원인을 넣지 않았다.
- 프로그램 검색: 기존 `backend/scripts/evaluate_search.py --case coupled_joint_limits --live`로 실제 JobRunner를 실행했다. UI 버튼 대신 같은 실행 경로를 호출했다. `agy / gemini-3.8-flash-medium`, `deep` 120초 및 `exhaustive` 300초를 각각 신규 실행했다.
- 평가용 문헌 식별자는 검색 입력에 전달하지 않는다. 기존 평가 사례의 청구항이 이번 입력과 동일함을 확인했다.
- 직접 검색과 프로그램은 모델·검색 서비스·시간 배분이 다르다. 프로그램 계획 캐시와 기존 진단 문서도 존재한다. 따라서 동일 조건의 블라인드 대결이나 통계적 성능 평가가 아닌, 현재 제품의 실제 결과와 직접 조사 결과의 비교다.
- 동일 청구항의 출처 문헌 재발견과 별개의 유사 선행기술 후보 발견을 분리했다. 기준일이 없으므로 프로그램에 날짜 제한을 임의로 추가하지 않았다.

## 직접 찾고 본문을 확인한 주요 문헌

### 1. 입력 청구항과 사실상 일치하는 특허

[JP7475618B1 — Method and program for determining posture of skeletal model](https://patents.google.com/patent/JP7475618B1/en)

첫 질의 `patent "child bone" "first limit" posture`에서 발견했다. 청구항 1에 부모·자식 뼈, 회전 파라미터, 판정→보정→자세 결정, 한 파라미터가 다른 둘 이상의 제한에 영향을 주는 조건이 모두 나타난다. 공개일은 2024-04-30, 표시된 우선일은 2023-12-04다. 이것은 출처 식별 성과이며 별개의 선행발명으로 집계하지 않는다. 원 청구항의 국가·출원번호가 제공되지 않아 사용자의 실제 출원과 동일 패밀리인지는 확정하지 않는다.

### 2. 핵심 유사 후보: 2012년 SDF 관절 제약 논문

[A joint-constraint model for human joints using signed distance-fields](https://erleben.github.io/pubs/2012/engell.noerregaard.ea12/engell.noerregaard.ea12.pdf), DOI [10.1007/s11044-011-9296-1](https://doi.org/10.1007/s11044-011-9296-1).

`joint limits / interdependencies / quaternion` 탐색에서 2010년 선행 버전과 이 논문의 제목을 얻은 뒤 제목 검색으로 저자 공개 PDF를 찾았다. PDF의 업로드 시점이나 검색엔진 수집일 대신 논문 서지를 기준으로 2012년 문헌으로 다뤘다.

| 청구항 요소 | 본문 근거 | 대응 판단 |
| --- | --- | --- |
| 전제: 부모·자식 뼈, 상대 회전 파라미터 | §1: 부모 뼈에 관절로 연결된 계층 골격, 상대 변환의 파라미터 | 명시적 대응 |
| A 및 D의 초과 판정 | §3.5 식 (5): 파라미터 벡터의 거리장 값으로 허용 여부 검사 | 강한 대응 |
| D의 파라미터 간 연동 | §2: 같은 관절 내부 파라미터의 완전한 의존성, 3차원 비독립 허용 영역 | 강한 후보. ‘하나→다른 둘 이상’은 영역의 기하와 파라미터 관계에서 해석해야 하므로 동일 문구의 명시 개시로 단정하지 않음 |
| B 및 E의 한계 내 보정 | §3.5 식 (7): `p ← p − Φ(p)∇Φ(p)`로 불허 자세를 허용 경계에 투영 | 명시적 보정 관계 |
| C의 자세 결정 | §3.5 및 §4: 보정된 제약을 IK 골격의 동작에 적용 | 강한 대응 |

위 표의 절 번호가 위치 기준이다. 저자 PDF는 ResearchGate 표지 한 장을 포함한다. 이 문헌을 우선 검토 후보로 추천하지만 단일 문헌에 의한 청구항 전체 충족 판정까지 완료한 것은 아니다.

### 3. 추가 유사 후보

- [Automatic Determination of Shoulder Joint Limits using Quaternion Field Boundaries (2003)](https://people.csail.mit.edu/rurtasun/publications/HerdaUHF03.pdf): 첫 개념 검색에서 발견했다. §1은 회전각 간 의존성을 명시하고, §5.1은 허용 회전 영역과 불허 자세의 경계 투영을 설명한다. 특히 Fig. 15의 보정 절차가 A/B/E와 가깝다. 사원수 공간에서 D의 정확한 파라미터 대응은 추가 대조가 필요하다.
- [Local Joint–Limits using Distance Field Cones in Euler Angle Space (2010)](https://erleben.github.io/pubs/2010/engell.noerregaard.ea10/engell.noerregaard.ea10.pdf): §III에서 세 Euler 각과 파라미터 의존성, 내부/외부 검사와 경계 투영을 설명한다. 2012년 논문과 이어지는 연구 계열이므로 독립된 세 발명을 찾았다고 과장하지 않는다.
- [Parametrization and Range of Motion of the Ball-and-Socket Joint](https://infoscience.epfl.ch/server/api/core/bitstreams/949101d1-ec81-4e38-91d8-c414ee95c8fc/content): 최초 웹 검색에서도 노출됐으며 원문 URL은 기존 평가 자료의 링크를 이용했다. §3.3의 swing에 따른 twist 제한, §4.1의 clamping이 관련된다. 다만 ‘두 swing 변수→한 twist 제한’과 청구항 D의 ‘하나→다른 둘 이상’은 그대로 같다고 볼 수 없다.
- [JP5490080B2 (2014)](https://patents.google.com/patent/JP5490080B2/en): 출처 특허의 인용망으로 찾았다. 표준 골격의 각도 제약을 대상 골격에 적용하는 흐름이 관련되지만, 현재 확인한 핵심은 모델 간 제약 적용이다. D의 연동 조건을 입증하는 문헌으로 선정하지 않았다.

기존 평가 자료의 [US9478058B2](https://patents.google.com/patent/US9478058B2/en)도 확인했다. 주요 내용은 사용자가 지정한 화면 위치로 샘플 속성을 보간하는 것이어서, 제목의 ‘correcting’만으로 이번 한계 초과 보정과 같은 발명으로 볼 수 없다. 이는 직접 신규 발견으로 세지 않았다.

## 프로그램의 새 실행 결과

실행 원본은 `%LOCALAPPDATA%/PRISM/runs/<job-id>/`에 보존했다.

| 항목 | deep 120초 | exhaustive 300초 |
| --- | --- | --- |
| 실행 ID | `03ff303a-5829-49c9-890e-326f38f91d58` | `29633f0e-0db7-4c40-ad00-d4c98463a6c5` |
| 엔진 소요 시간 | 110.687초 | 279.797초 |
| 후보 | 20개, 모두 OpenAlex | 동일 20개, 모두 OpenAlex |
| EPO 초기 두 질의 | 모두 0건 | 모두 0건 |
| 웹 검색 | 7회 호출, 약 54.4초 후 timeout, 저장 후보 0개 | 35회 호출, 약 245.1초 후 timeout, 저장 후보 0개 |
| 분류 | 8개 검토: Z 2, 정보 부족 5, 관련성 낮음 1 | 8개 검토: Z 1, 정보 부족 3, 관련성 낮음 4 |
| 원문·검증 인용 | 0개 / 0개 | 0개 / 0개 |
| 위 주요 논문 3개 및 출처 특허 | 모두 후보 목록에 없음 | 모두 후보 목록에 없음 |
| 캐시 | 계획 캐시 사용, 질의 캐시 0 | 계획 캐시 사용, 질의 캐시 4 |

120초 실행의 상위 결과는 `Estimation of skeletal kinematics in freely moving rodents`, `VNect`, `Model-Based 3D Hand Pose Estimation from Monocular Video` 등이다. 분야가 가까운 문헌은 있으나 D/E를 원문으로 확인한 결과는 없다. Job의 `SUCCEEDED`는 작업 종료 상태이며 manifest는 `verification_incomplete`다. 8개 분류 완료가 20개 전부 검토나 선행문헌 확보 성공을 의미하지 않는다.

300초 실행에서도 manifest는 `verification_incomplete`다. 35개 웹 도구 이벤트는 모두 `search_web`이고 페이지 열람 이벤트는 없다. 18번째 질의에는 Herda, 25번째에는 ball-and-socket 논문 제목이 등장했다. 따라서 프로그램이 관련 연구 방향을 전혀 떠올리지 못했다고 단정할 수 없다. **관련 단서로 탐색하는 것과 확인 가능한 후보·URL·근거를 최종 결과로 남기는 것 사이에서 실패했다.** 도구 기록의 제목 등장만으로 원문 확보나 독립 검증 성공으로 세지 않았다.

두 실행은 동일 후보 집합에도 잠정 분류가 달랐다. 이는 모델 분류의 변동이며 검색 품질이 개선됐다는 증거가 아니다. 첫 후보 시간 8.516초와 0.297초 역시 두 번째 실행의 질의 캐시 영향이 있어 속도 향상률로 비교하지 않는다. 프로세스 종료 시 Windows asyncio pipe 정리 경고가 발생했지만, 두 작업의 지표·manifest 저장과 종료 코드 0을 확인했다.

## 같은 검색 API로 질의만 바꾼 보조 실험

자동 검색의 후보에 주입하지 않고 별도 진단으로 실행했다. `quaternion`, `distance fields`는 직접 읽은 문헌에서 얻은 확장어이므로 청구항만으로 생성한 독립 검색 성적으로 계산하지 않는다.

| API / 검색식 | 실제 결과 | 의미 |
| --- | --- | --- |
| 자동 EPO: `coupled joint limit skeletal rotation correction` | 0건 | 6개 단어의 제목·초록 all 조건이 너무 좁을 가능성 |
| 자동 EPO: `interdependent range motion kinematic pose constraint` | 0건 | 대체 질의도 실제 후보 없음 |
| OpenAlex: `joint limits dependencies` | 전체 419,083건, 상위권 번역·언어 의존성 논문 등 | 짧게 줄이는 것만으로 해결되지 않음 |
| 위 질의, 제목·초록 모드 | 전체 1,265건, 3위에 2005년 hierarchical implicit surface 논문 | 색인 범위를 바꾸면 다른 관련 후보 발견 |
| OpenAlex: `joint limits quaternion` | **2003년 핵심 논문 1위** | 같은 API도 적절한 확장어로 발견 가능 |
| OpenAlex: `joint constraints distance fields` | **2012년 핵심 논문 4위** | 새 기술 용어의 후속 검색 효과 |
| EPO: `skeletal posture parameters` | 10건, 핵심 출처 특허 확인 안 됨 | 결과 수 증가 자체는 품질 개선이 아님 |
| EPO: `bone rotation limit` | 159건 중 20건 반환, 대부분 의료 기구, 18위에 자세 결정 관련 CN 문헌 | 짧은 질의는 후보 확보와 잡음 증가를 함께 일으킴. CN 문헌의 동일 패밀리 여부는 미검증 |

OpenAlex `joint constraints projection`은 넓은 검색과 제목·초록 모드 모두 핵심 논문을 상위 10개에 포함하지 못했다. 특정 키워드나 모드를 모든 사건에 고정할 근거는 없다. 첫 페이지에서 찾지 못한 것을 DB 전체에 없다고 해석하지 않는다.

## 차이와 수정 우선순위

1. **웹 탐색 결과를 중간에 보존해야 한다.** 현재 `web_seeds()`는 모델이 최종 `records`를 반환한 뒤 후보를 추가한다. 검색 호출이 여러 번 이루어져도 최종 응답 전에 시간 초과되면 후보가 0개다. 도구 출력에서 확인 가능한 제목·URL·출처를 점진적으로 남기고, 미검증 상태로 후속 대조에 넘기는 방식이 우선이다. 실패한 실행 로그의 검색어에 번호가 등장했다는 것만으로 해당 문헌을 ‘검색 성공’ 처리해서는 안 된다.
2. **DB의 빈 결과와 잡음을 보고 질의를 바꿔야 한다.** 현재 두 seed를 EPO 제목·초록 all 검색과 OpenAlex 검색에 공통 사용한다. EPO는 0건, OpenAlex는 잡음 많은 후보를 반환해도 자동 DB 질의의 의미를 재설계하지 않는다. `expand()`도 같은 두 질의를 EPO의 더 넓은 서지 필드로 다시 보낸다. 본문에서 발견한 quaternion/implicit surface/distance field 같은 표현을 후속 질의 후보로 만들되, 입력의 필수 한정으로 추가해서는 안 된다.
3. **검색 서비스별 구문·색인 범위를 분리해야 한다.** OpenAlex에는 제목·초록 모드가 구현되어 있지만 `Sources.search()` 기본 경로는 선택하지 않는다. 검색 연산자 정리 함수가 따옴표도 제거하므로 구문 검색의 의미 보존도 검토해야 한다. 이번 실험은 구문 보존 개선의 효과까지 검증하지 않았다.
4. **검색과 원문 검토가 모두 가능한 시간 배분이 필요하다.** 현재 수집 마감은 분류 시간만 예약한다. 웹에 `remaining()` 전체를 줄 수 있어 DB 질의 수정·인용망·본문 검토의 여유가 사라진다. 이번에는 두 실행 모두 원문 수집 시작 조건인 `remaining() >= 20`을 충족하지 못해 수집 시도도 없었다. 단순히 호출 횟수를 다시 작게 고정하는 것보다, 후보를 저장하면서 탐색하고 충분한 후보가 생기면 원문 대조로 전환하는 동작이 중요하다.
5. **핵심 관계로 평가하고 출처 재발견을 따로 표시해야 한다.** 일반 자세 추정·관절 제한을 찾았다는 사실과 D/E를 찾았다는 사실은 다르다. 한정 D의 의존 방향과 개수까지 별도로 확인해야 한다. 동일 청구항 특허, 유사한 이전 연구, 관련성이 낮은 결과, 자료 부족을 각각 구분할 필요가 있다.

KIPRIS는 실제 호출에서 API 오류를 반환했다. 키·서비스 승인·한도 중 원인은 이번 응답만으로 특정할 수 없다. EPO의 0건 및 웹 timeout과 분리된 채널 문제이며, 국내 특허를 정상 검색했다고 볼 수 없다.

코드 위치: `backend/app/search_engine/engine.py`의 `web_seeds`, `discover`, `expand`, `run`; `sources.py`의 `search`; `patent_search/openalex_client.py`의 `search_url`; `patent_search/literature_client.py`의 `plain_query`.

## 재현 자료

- [실행별 후보·질의·도구 호출·발견 여부](artifacts/joint-search-comparison-20260917/run-comparison.json)
- [120초 실행 지표](artifacts/joint-search-comparison-20260917/deep-metrics.json)
- [300초 실행 지표](artifacts/joint-search-comparison-20260917/exhaustive-metrics.json)
- [질의 변경 실측](artifacts/joint-search-comparison-20260917/query-probes.json)
- [색인 범위·추가 확장어 실측](artifacts/joint-search-comparison-20260917/scope-probes.json)
- [질의 실험 스크립트](artifacts/joint-search-comparison-20260917/probe_queries.py), [실행 결과 수집 스크립트](artifacts/joint-search-comparison-20260917/collect_runs.py)

기존 평가 지표의 known-relevant 목록은 ball-and-socket 논문 한 편뿐이다. 그 recall 값은 전체 관련 문헌의 재현율이 아니다. 이번에 직접 찾은 주요 논문의 포함 여부는 별도 `lead_ranks`로 보존했다.

부가 실험 `scope-probes.json`은 3개 응답이 모두 저장됐다. 마지막 응답의 콘솔 출력에서 CP949 인코딩 오류가 났으나 JSON 파일의 UTF-8 저장과 결과 내용은 확인했다. 산출물 JSON의 파싱, 두 실행의 후보 집합·문헌 누락·웹 호출 수 대조 및 `git diff --check`를 수행했다. 제품 코드는 변경하지 않았으므로 제품 회귀 테스트는 새로 실행하지 않았다.
