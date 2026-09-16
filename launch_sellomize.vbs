Set WshShell = CreateObject("WScript.Shell")
strPath = WshShell.CurrentDirectory

' Launch Streamlit Dashboard silently (0 = hidden window)
WshShell.Run "cmd /c cd /d """ & strPath & """ && python -m streamlit run app.py", 0, False

' Launch Outlook Scheduler silently (0 = hidden window)
WshShell.Run "cmd /c cd /d """ & strPath & """ && python scheduler.py", 0, False

' Wait 2 seconds and open default browser
WScript.Sleep 2500
WshShell.Run "http://localhost:8501"
