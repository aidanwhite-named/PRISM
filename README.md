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

소스 실행 방법은 아래와 같습니다. Windows EXE와 설치 프로그램을 만드는
빌드 스크립트는 [배포 안내](docs/windows-distribution.md)에 정리되어 있습니다.

Windows 10/11, Python 3.11 이상, Node.js 18 이상과 사용할 AI CLI 하나를 준비합니다.
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

## 배포와 개발

권장 배포 방식은 **Python 런타임을 포함한 PRISM EXE + 필요한 AI CLI 별도 설치**입니다.
프론트엔드는 빌드 결과만 포함하고, AI CLI와 로그인 정보는 배포 파일에 넣지 않습니다.
다른 Windows PC에서 만드는 방법은 [Windows EXE 배포 안내](docs/windows-distribution.md)를 참고하세요.

- [상세 사용법·설계·테스트 안내](docs/technical-guide.md)
- [KIPRIS 연동 안내](docs/kipris-integration.md)

현재 저장소에는 별도 라이선스 파일이 없습니다. 배포 전에 프로젝트 라이선스를 정해야 합니다.
