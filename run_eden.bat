@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ================================================
echo   Eden 로컬 실행 스크립트 (Windows)
echo ================================================
echo.

if not exist "streamlit_app.py" (
    echo [오류] streamlit_app.py 를 찾을 수 없습니다.
    echo        이 배치파일을 프로젝트 루트(streamlit_app.py가 있는 폴더)에 두고 실행하세요.
    goto :END_ERROR
)

REM ────────────────────────────────────────────────
REM 1. 가상환경 생성
REM ────────────────────────────────────────────────
echo [1/5] 가상환경 확인/생성 중...

if exist ".venv\Scripts\python.exe" (
    echo       기존 .venv 를 사용합니다.
    goto :ACTIVATE_VENV
)

where uv >nul 2>nul
if not errorlevel 1 (
    echo       uv 로 가상환경을 생성합니다 (.venv, Python 3.13)...
    uv venv .venv --python=3.13
    goto :CHECK_VENV
)

where python >nul 2>nul
if not errorlevel 1 (
    echo       uv 가 없어 python -m venv 로 생성합니다...
    python -m venv .venv
    goto :CHECK_VENV
)

where py >nul 2>nul
if not errorlevel 1 (
    echo       uv/python 이 없어 py -3.13 -m venv 로 생성합니다...
    py -3.13 -m venv .venv
    goto :CHECK_VENV
)

echo [오류] uv / python / py 중 아무것도 찾을 수 없습니다. Python 3.13을 먼저 설치하세요.
goto :END_ERROR

:CHECK_VENV
if not exist ".venv\Scripts\python.exe" (
    echo [오류] 가상환경 생성에 실패했습니다. 위 로그를 확인하세요.
    goto :END_ERROR
)

:ACTIVATE_VENV
call ".venv\Scripts\activate.bat"

REM ────────────────────────────────────────────────
REM 2. requirements 설치 (uv 우선, 없으면 pip)
REM ────────────────────────────────────────────────
echo.
echo [2/5] 필수 라이브러리 설치 중 (requirements.txt)...

where uv >nul 2>nul
if not errorlevel 1 (
    uv pip install -r requirements.txt
) else (
    python -m pip install --upgrade pip >nul 2>nul
    pip install -r requirements.txt
)

if errorlevel 1 (
    echo [오류] 라이브러리 설치 중 오류가 발생했습니다. 위 로그를 확인하세요.
    goto :END_ERROR
)

REM ────────────────────────────────────────────────
REM 3. 환경 변수(.env) 준비
REM ────────────────────────────────────────────────
echo.
echo [3/5] 환경 변수(.env) 확인 중...

if exist ".env" (
    echo       기존 .env 파일이 있습니다. 필요하면 값을 확인/수정하세요.
) else (
    (
        echo OPENAI_API_KEY=sk-...
        echo LLM_MODEL=gpt-4o-mini
        echo(
        echo NEO4J_URI=neo4j+s://^<your-instance^>.databases.neo4j.io
        echo NEO4J_USER=neo4j
        echo NEO4J_PASSWORD=
        echo NEO4J_DATABASE=neo4j
        echo(
        echo # 로컬 개발은 QDRANT_URL 을 비워두면 자동으로 Chroma 를 사용합니다.
        echo QDRANT_URL=
        echo QDRANT_API_KEY=
        echo QDRANT_COLLECTION=bible_verses
    ) > .env
    echo       .env 템플릿을 새로 생성했습니다.
)

echo       메모장에서 .env 파일을 엽니다. OPENAI_API_KEY 등 필요한 값을 입력하고 저장하세요.
notepad .env

echo.
set "ENV_CONFIRM="
set /p ENV_CONFIRM=값을 모두 입력하고 저장하셨습니까? 계속하려면 Y, 중단하려면 N 을 입력하세요:

if /i "!ENV_CONFIRM!"=="Y" goto :CHECK_DATA
if /i "!ENV_CONFIRM!"=="N" (
    echo       사용자가 중단을 선택했습니다. 스크립트를 종료합니다.
    goto :END_OK
)

echo [오류] Y 또는 N 만 입력 가능합니다.
goto :END_ERROR

REM ────────────────────────────────────────────────
REM 4. 데이터 파일 배치 확인
REM ────────────────────────────────────────────────
:CHECK_DATA
echo.
echo [4/5] 데이터 파일 확인 중 (data\bible_structured.json)...

if not exist "data\bible_structured.json" (
    echo [오류] data\bible_structured.json 파일이 없습니다.
    echo        아래 원본을 받아 위 경로에 저장한 뒤 다시 실행하세요:
    echo        https://raw.githubusercontent.com/stranger828/bibleAPI/refs/heads/main/bible_structured.json
    goto :END_ERROR
)
echo       확인 완료.

REM ────────────────────────────────────────────────
REM 5. Streamlit 앱 실행
REM ────────────────────────────────────────────────
echo.
echo [5/5] Eden 을 실행합니다 (streamlit run streamlit_app.py)...
echo       최초 실행 시 로컬 Chroma 임베딩 작업으로 수 분이 걸릴 수 있습니다.
echo.
streamlit run streamlit_app.py

goto :END_OK

:END_ERROR
echo.
echo 스크립트가 오류로 중단되었습니다.
pause
endlocal
exit /b 1

:END_OK
pause
endlocal
exit /b 0
