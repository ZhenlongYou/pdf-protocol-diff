@echo off
setlocal

cd /d "%~dp0"
py -3 -m pip install -r requirements.txt
if errorlevel 1 exit /b %errorlevel%
py -3 -m pip install -r requirements-build.txt
if errorlevel 1 exit /b %errorlevel%
py -3 build_desktop.py --clean
if errorlevel 1 exit /b %errorlevel%

echo.
echo Windows exe created at: dist\ProtocolPdfDiff.exe
pause
