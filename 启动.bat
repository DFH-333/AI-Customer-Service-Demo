@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo 没有找到 python，请先安装 Python 3 并勾选 Add to PATH。
  echo.
  pause
  exit /b 1
)
echo 正在启动 AI 客服演示...
python app.py
echo.
pause
