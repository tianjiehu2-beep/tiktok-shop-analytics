@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 启动 TikTok Shop 数据分析看板 ...
echo 浏览器会自动打开 http://localhost:8501 ，按 Ctrl+C 退出。
D:\Python\python.exe -m streamlit run ui/app.py
pause
