# PRISM

특허 구성대비 분석과 유사문헌 검색을 위한 로컬 웹 프로그램입니다.
첨부 문서를 선택한 AI CLI에 전달하고, 결과와 실행 이력을 PC에 저장합니다.
화면은 브라우저에서 열리며, AI 실행과 외부 문헌 검색에는 인터넷 연결이 필요합니다.

## 주요 기능

- 특허 구성대비 분석, 특허·논문 유사문헌 검색
- 프롬프트 편집과 첨부 문서 관리
- Claude Code, Codex CLI, agy 연동
- 결과 열람·내보내기와 실행 이력 보관

## 시작하기

배포 ZIP을 받은 경우 압축을 풀고 **설치.cmd → 실행.cmd** 순서로 실행합니다.
설치를 누르면 선택 입력 없이 Python·라이브러리·Node.js·Claude Code·Codex를 준비합니다.
진행 상황은 설치 창에서 보여줍니다. 구현 파일은 ZIP의 `app` 폴더에 모았습니다.
자세한 내용은 [사용안내.txt](사용안내.txt)를 참고하세요.

소스 실행 방법은 아래와 같습니다.

Windows 10/11 x64, Python 3.11/3.12 x64, Node.js LTS와 사용할 AI CLI 하나를 준비합니다.
각 CLI의 실행 조건은 해당 도구의 설치 안내를 따릅니다.

프로젝트 폴더의 PowerShell에서 최초 한 번 실행합니다.

```powershell
.\start-prism.ps1 -Setup
```

이후에는 다음 명령으로 실행합니다.

```powershell
.\start-prism.ps1
```

브라우저에서 `http://127.0.0.1:8765`가 열립니다. 포트가 사용 중이면 다른 포트를 선택합니다.
종료하려면 실행한 PowerShell에서 `Ctrl+C`를 누릅니다.
프론트엔드 변경 후에는 `-Rebuild`, 포트 지정은 `-Port 9000`을 사용합니다.

## 사용 순서

1. 사용할 AI CLI를 설치합니다. 모든 CLI를 설치할 필요는 없습니다.
2. **Settings → AI 실행 도구 상태**에서 경로를 확인하고 로그인한 뒤 다시 검사합니다.
3. 분석 또는 검색 화면에서 프롬프트, 모델과 입력 자료를 선택해 실행합니다.
4. 결과를 확인하고 **History**에서 이전 실행을 열람합니다.

AI 실행은 각 CLI의 로그인 세션과 계정 사용량을 사용합니다.
EPO·KIPRIS·OpenAlex 등의 검색 서비스 자격증명은 Settings에서 별도로 설정합니다.

## 데이터와 제한사항

- 실행 이력과 설정: `%LOCALAPPDATA%\PRISM` (`PRISM_DATA_DIR`로 변경 가능)
- 편집 가능한 프롬프트: 프로젝트의 `prompt` 폴더 (`PRISM_PROMPT_DIR`로 변경 가능)
- 로컬 프로그램이지만, 선택한 AI 서비스로 입력 자료가 전송됩니다.
- 스캔 PDF의 OCR과 DOCX·XLSX·이미지 첨부는 지원하지 않습니다.
- 의미 검색은 선택 기능이며 기본 설치에 포함되지 않습니다.
- CLI마다 도구 제어 범위가 다릅니다. Codex·agy의 파일·명령 도구는 PRISM이 완전히 차단하지 못합니다.
- AI의 분석과 검색 결과는 원문 근거와 함께 확인해야 합니다.

## 개발 안내

- 배포 ZIP 생성: `powershell -NoProfile -ExecutionPolicy Bypass -File .\build-release.ps1`
- 결과: `release/PRISM-<버전>-windows-x64.zip` 및 SHA-256 파일
- 프론트엔드를 `npm ci`와 `npm run build`로 빌드하고, 실행에 필요한 소스만 포함합니다.
- `.venv`, `node_modules`, DB·로그인 정보·개인 설정은 포함하지 않습니다.
- 프롬프트는 로컬 편집본 대신 Git HEAD의 기본 템플릿을 포함합니다.
- ZIP 설치는 `setup.ps1`, 개발 환경 설치·화면 빌드는 `start-prism.ps1 -Setup`을 사용합니다.
- 설치 실패·기존 CLI 재사용 검증: `python scripts/test_windows_setup.py`
- ZIP 설치·실행 검증: `python scripts/smoke_release.py release/PRISM-2.0.1-windows-x64.zip`
  임시 가상환경에 의존성을 실제 설치합니다. 시스템 Python·CLI 설치와 계정 로그인은 수행하지 않습니다.

- [상세 사용법·설계·테스트 안내](docs/technical-guide.md)
- [KIPRIS 연동 안내](docs/kipris-integration.md)

현재 저장소에는 별도 라이선스 파일이 없습니다. 배포 전에 프로젝트 라이선스를 정해야 합니다.
