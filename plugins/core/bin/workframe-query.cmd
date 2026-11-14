@echo off
python -B "%~dp0workframe-query" %*
exit /b %ERRORLEVEL%
