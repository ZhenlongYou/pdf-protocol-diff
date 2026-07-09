@echo off
REM 关闭命令回显，让用户只看到关键安装、构建和错误信息。
setlocal
REM 使用局部环境变量，避免批处理修改影响用户后续命令行窗口。

cd /d "%~dp0"
REM 切换到这个 bat 所在目录，保证相对路径都指向项目根目录。
py -3 -m pip install -r requirements.txt
REM 安装运行依赖，例如 PDF 解析、表格截图和 OCR Python 包。
if errorlevel 1 exit /b %errorlevel%
REM 如果运行依赖安装失败，立刻返回原始错误码。
py -3 -m pip install -r requirements-build.txt
REM 安装 PyInstaller 等构建依赖。
if errorlevel 1 exit /b %errorlevel%
REM 如果构建依赖安装失败，立刻返回原始错误码。
py -3 build_desktop.py --clean --onefile
REM 使用 --onefile 生成单个 dist\ProtocolPdfDiff.exe，方便直接分发给 Windows 用户。
if errorlevel 1 exit /b %errorlevel%
REM 如果 PyInstaller 构建或冻结后自检失败，立刻返回原始错误码。

echo.
REM 输出空行，分隔安装日志和最终产物提示。
echo Windows exe created at: dist\ProtocolPdfDiff.exe
REM 告诉用户最终 exe 的相对路径。
pause
REM 暂停窗口，方便双击 bat 的用户看清最终结果或错误信息。
