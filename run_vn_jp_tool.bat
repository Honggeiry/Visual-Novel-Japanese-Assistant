@echo off
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0run_vn_jp_tool.ps1"
