# Windows 설치 프로그램 빌드 · v2.0.0

## 빌드 환경

- Windows 10/11 x64
- CPython 3.12.10 x64
- Inno Setup 7.0.1-beta x64
- 앱·빌드 의존성은 `requirements-build.txt`에서 버전 고정

Inno Setup은 빌드 스크립트가 공식 릴리스 파일을 내려받고 SHA-256을 확인함.

저장소 루트에서 PowerShell 실행:

```powershell
.\packaging\build.ps1
```

스크립트 처리 순서:

1. `work/`에 격리된 빌드 환경 생성 및 테스트 실행.
2. PyInstaller 앱과 `outputs/` 설치 프로그램 빌드.
3. 설치 프로그램 SHA-256 생성.
4. 임시 경로에 설치 후 앱 자체 검사와 Tk 시작 검사 실행.
5. 시작 메뉴·바탕화면 바로가기와 아이콘 확인 후 제거.

기존 설치 경로나 같은 이름의 바탕화면 바로가기가 있으면 설치 검증을 중단함. 빌드 로그와 테스트 설치 파일은 무시 대상 `work/`에 보관.

도구 경로를 직접 지정:

```powershell
.\packaging\build.ps1 -Iscc 'C:\path\ISCC.exe'
.\packaging\build.ps1 -Python 'C:\path\python.exe'
```

## 설치 동작

- 새 전체 사용자 설치 기본 경로: Inno Setup `{autopf}` 기반 `Program Files\Decap Adjustment`.
- 설치 및 업그레이드 화면에서 다른 드라이브나 폴더를 선택 가능.
- 기존 사용자별 설치는 설치 모드를 유지.
- Python과 Excel은 최종 사용자 PC에 필요하지 않음.
- 제거: Windows 설정의 설치된 앱.
- 설치 프로그램은 서명되지 않음. Windows SmartScreen이 알 수 없는 앱 경고를 표시할 수 있음.

앱 v2.0.0은 SPD를 열어 상태·배치를 편집하고 새 SPD로 저장함. `.xlsx`는 증감 명령 가져오기에만 사용하며, 별도 검증·감사 Excel 보고서는 생성하지 않음.

## 릴리스 검증

`v*` Git 태그 push 시 `.github/workflows/windows-release.yml`이 테스트·패키징·설치·실행·제거를 검증함. 태그 버전은 `pyproject.toml`의 버전과 일치해야 함. 성공 시 설치 프로그램, SHA-256 파일, 검증 요약을 GitHub Release에 게시.

재현 가능한 빌드는 고정된 도구와 절차를 뜻함. 타임스탬프와 Windows 도구 체인 차이로 바이너리 바이트까지 같다는 보장은 없음. Windows on ARM과 다른 Windows 버전은 검증하지 않음.
