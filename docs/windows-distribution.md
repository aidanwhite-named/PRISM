# Windows EXE 배포

Python 런타임과 기본 라이브러리를 포함하는 Windows x64 배포용 소스·스크립트입니다.
빌드 결과물은 Git에 포함하지 않습니다. 사용할 AI CLI는 수신자가 별도로 설치·로그인합니다.

## 다른 PC에서 빌드

Windows x64에서 Python 3.11 x64, Node.js/npm을 준비합니다.
저장소 루트의 PowerShell에서 실행합니다.

```powershell
# 설치 없이 압축을 풀어 사용하는 EXE + ZIP
.\build-windows.ps1 -SkipInstaller

# Inno Setup을 별도로 설치한 경우 설치 EXE도 생성
.\build-windows.ps1 -IsccPath 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

빌드 스크립트는 `.build-venv`에 빌드 환경을 구성하고 프론트엔드를 빌드한 뒤
PyInstaller의 폴더형 배포를 만듭니다. Inno Setup을 자동으로 설치하지 않습니다.

| 결과물 | 위치 |
|---|---|
| 실행 폴더 | `dist/PRISM` |
| 압축 배포본 | `release/PRISM-<version>-windows-x64.zip` |
| 설치 프로그램 | `release/PRISM-Setup-<version>-windows-x64.exe` (Inno Setup 필요) |
| 파일 해시 | `release/SHA256SUMS.txt` |
| 빌드 시 사용한 Python 패키지 버전 | `release/build-requirements.txt` |

## 사용자 실행

ZIP은 **폴더 전체를 압축 해제**하고 `PRISM.exe`를 실행합니다. EXE만 따로 옮기면 안 됩니다.
기본 브라우저에 화면이 열리며 실행 콘솔은 유지됩니다.

- 종료: 콘솔에서 `Ctrl+C`, 또는 `PRISM.exe --stop`
- 재실행: 기존 실행 화면 열기
- 옵션: `--port 9000`, `--no-browser`
- 브라우저 탭을 닫아도 서버는 계속 실행됩니다.

Python은 포함되어 있으므로 사용자가 설치할 필요가 없습니다.
Node.js는 선택한 AI CLI가 요구하는 경우에만 필요합니다.
Claude Code·Codex CLI·agy 중 사용할 도구를 별도로 설치하고 Settings에서 로그인합니다.

## 구현 구성

- `backend/desktop.py`: 로컬 서버, 준비 완료 후 브라우저 열기, 중복 실행 방지, 종료 명령
- `backend/app/runtime.py`: 배포 자산 경로, 기본 프롬프트 복사, 외부 CLI를 위한 DLL 경로 처리
- `PRISM.exe --search-mcp`: 검색 전용 표준 입출력 프로세스. 별도 Python 설치 없이 실행
- `packaging/prism.spec`: 백엔드·기본 Python 의존성·프론트엔드 빌드 결과·기본 프롬프트 수집
- `packaging/prism.iss`: 사용자별 설치와 시작 메뉴 바로가기, 제거 시 사용자 데이터 보존
- `packaging/collect_notices.py`: 의존성 버전과 제공된 라이선스 문서 수집

데이터는 `%LOCALAPPDATA%\PRISM`, EXE의 편집 가능한 프롬프트는 그 아래 `prompts`에 저장합니다.
최초 실행 시 없는 기본 프롬프트만 복사하며 기존 편집본은 덮어쓰지 않습니다.
`PRISM_DATA_DIR`와 `PRISM_PROMPT_DIR`로 위치를 변경할 수 있습니다.
소스 실행 시 프롬프트 기본 위치는 기존 프로젝트의 `prompt`입니다.

의미 검색 라이브러리·모델, AI CLI, 개발자의 인증 정보·DB·실행 이력은 포함하지 않습니다.
현재 빌드는 코드 서명 설정이 없으며, 공개 배포 전 프로젝트 라이선스를 정해야 합니다.

## 선택 검증

빌드와 별도로 실행하며 실제 모델을 호출하지 않습니다.

```powershell
python packaging/smoke_windows.py
```

한글·공백 설치 경로, Python·Node.js가 없는 PATH, 포트 충돌, UI 자산,
MCP 초기화·도구 목록, 중복 실행, 종료·재시작과 프롬프트 보존을 확인합니다.
실제 CLI 로그인·모델 호출과 별도 깨끗한 Windows PC에서의 검증은 별도로 필요합니다.

참고: [PyInstaller 배포 방식](https://pyinstaller.org/en/stable/operating-mode.html),
[외부 프로그램 실행](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html),
[Inno Setup 사용자별 설치](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm).
