@echo off
cd /d "%~dp0"
if not exist "runtime\python.exe" (
  echo 这里还没有带好的运行环境。
  echo 请下载 Choirian-windows.zip，解压后再双击这个文件。
  pause
  exit /b 1
)
set HOMR_PYTHON=%~dp0runtime\python.exe
set CHOIRIAN_OPEN_BROWSER=1
echo 正在打开 Choirian。
echo 关掉这个窗口就会退出。
"runtime\python.exe" run.py
echo.
echo 程序已退出。
pause
