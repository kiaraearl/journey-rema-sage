Option Explicit

Dim WshShell, sProject, sEdge
Set WshShell = CreateObject("WScript.Shell")

sProject = "C:\Users\Kimea\Projects\job-apps"
sEdge    = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

' Start Flask server silently (pythonw = no console window)
WshShell.CurrentDirectory = sProject
WshShell.Run "pythonw """ & sProject & "\dashboard.py""", 0, False

' Give Flask time to bind to port 5000
WScript.Sleep 2800

' Open Edge in app mode (no URL bar, no tabs — looks like a native app)
WshShell.Run """" & sEdge & """ --app=http://localhost:5000 --new-window", 1, False

Set WshShell = Nothing
