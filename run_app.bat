@echo off
title Sellomize Reach - UI
cd /d "%~dp0"
echo ========================================================
echo Starting Sellomize Reach Email Automation Studio...
echo ========================================================
python -m streamlit run app.py
pause
