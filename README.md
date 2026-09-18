# PRISM

특허 구성대비 분석 및 유사문헌(특허·논문) 검색을 지원하는 로컬 웹 애플리케이션입니다.  
로컬 환경에서 AI CLI(Claude Code, Codex, agy)와 연동하여 분석을 수행하고, 결과와 실행 이력을 PC에 보관합니다.

---

## 주요 기능

- **특허 분석 & 검색**: 청구항 구성대비표 작성, 특허·논문 유사문헌 탐색
- **AI CLI 연동**: Claude Code, Codex CLI, agy 연동 지원
- **문서 & 프롬프트 관리**: PDF 첨부 문서 분석, 커스텀 프롬프트 템플릿 관리
- **로컬 데이터 보관**: 결과 열람/내보내기 및 실행 이력 PC 로컬 보관

---

## 시작하기

### 1. 배포본 (ZIP)
1. ZIP 압축을 풉니다.
2. **`설치.cmd`** 실행 (Python, 의존성, Node.js, CLI 자동 설치)
3. **`실행.cmd`** 실행 → 브라우저(`http://127.0.0.1:8765`) 자동 연결  
*(자세한 내용은 [사용안내.txt](사용안내.txt) 참고)*

자동 설치에 필요한 WinGet이 없으면 Microsoft 공식 GitHub 배포본과 필수 패키지를 설치하고 이어서 진행합니다. Microsoft Store는 필요하지 않습니다. 회사 정책이나 네트워크에서 설치를 차단하는 경우에는 IT 관리자 지원이 필요합니다.

### 2. 소스코드 직접 실행
> **필수 요구사항**: Windows 10/11 x64, Python 3.11/3.12 x64, Node.js LTS

```powershell
# 최초 환경 설정 (가상환경 구성, 패키지 설치, UI 빌드)
.\start-prism.ps1 -Setup

# 서버 실행 (종료: Ctrl + C)
.\start-prism.ps1
```
* 옵션: `-Port <포트번호>`, `-Rebuild` (프론트엔드 재빌드)

---

## 기본 사용법

1. **AI CLI 로그인**: 사용할 AI CLI의 계정 로그인을 완료합니다.
2. **상태 확인**: 웹 화면의 **Settings → AI 실행 도구 상태**에서 연동 상태를 확인합니다.
   - EPO, KIPRIS, OpenAlex 등 외부 검색 API 키도 Settings에서 등록 가능합니다.
3. **분석/검색 실행**: 프롬프트 및 분석 대상 문서를 선택해 작업을 실행합니다.
4. **결과 확인**: 결과 열람 및 내보내기, **History** 탭에서 이전 기록 확인.

---

## 주요 안내 및 주의사항

- **데이터 저장 경로**:
  - 실행 이력 및 설정: `%LOCALAPPDATA%\PRISM` (`PRISM_DATA_DIR`로 변경 가능)
  - 편집 프롬프트: `prompt/` 폴더 (`PRISM_PROMPT_DIR`로 변경 가능)
- **문서 지원**: 텍스트 기반 PDF를 지원하며 스캔 이미지 PDF(OCR), DOCX, XLSX 등은 지원하지 않습니다.
- **네트워크 & 계정**: AI 실행 및 문헌 검색 시 인터넷 연결과 해당 AI 서비스 계정 할당량이 사용됩니다.

---

## 개발 및 빌드

```powershell
# 릴리즈 ZIP 패키지 생성
powershell -ExecutionPolicy Bypass -File .\build-release.ps1

# 설치 및 릴리즈 검증
python scripts/test_windows_setup.py
python scripts/smoke_release.py release/PRISM-2.0.0-windows-x64.zip
```

---

## 참고 문서

- [기술 및 아키텍처 가이드](docs/technical-guide.md)
- [KIPRIS 연동 안내](docs/kipris-integration.md)

