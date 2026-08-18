@echo off
chcp 65001 >nul
REM Workframe python launcher (Windows)
REM Selection order: python -> py -3 -> python3
REM Each candidate must satisfy sys.version_info[0] == 3 (probe before exec),
REM otherwise fall through to the next candidate. This avoids:
REM   - Microsoft Store python.exe alias (no real Python 3 installed)
REM   - Legacy Python 2.x on PATH
REM chcp 65001 switches the console code page to UTF-8 so Python scripts that
REM emit Chinese / non-ASCII output (via TextIOWrapper utf-8) render correctly
REM in cmd.exe instead of becoming mojibake under the default cp936/cp1252.
REM Falls back to exit 0 with stderr message when no Python 3 is found, so
REM hooks do not block the Claude session.

set "PROBE=import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)"

where python >nul 2>nul
if errorlevel 1 goto try_py
python -c "%PROBE%" >nul 2>nul
if errorlevel 1 goto try_py
python %*
exit /b %ERRORLEVEL%

:try_py
where py >nul 2>nul
if errorlevel 1 goto try_python3
py -3 -c "%PROBE%" >nul 2>nul
if errorlevel 1 goto try_python3
py -3 %*
exit /b %ERRORLEVEL%

:try_python3
where python3 >nul 2>nul
if errorlevel 1 goto fail
python3 -c "%PROBE%" >nul 2>nul
if errorlevel 1 goto fail
python3 %*
exit /b %ERRORLEVEL%

:fail
echo [workframe-python] ERROR: no Python 3 interpreter found. 1>&2
echo [workframe-python]        Tried python, py -3, python3; each must satisfy sys.version_info[0] == 3. 1>&2
echo [workframe-python]        Hook skipped to avoid blocking the session. 1>&2
exit /b 0
