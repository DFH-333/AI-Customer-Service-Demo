@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo 找不到 python，先装 Python 或者把 python 加到 PATH。
  pause
  exit /b 1
)
python app.py "客户-驾校\知识库.md"
pause
