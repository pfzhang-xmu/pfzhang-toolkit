' Paper Workbench autostart (idempotent): start 8123 server only if not already up.
' Uses %USERPROFILE% to avoid hardcoded non-ASCII path (VBS reads ANSI, not UTF-8).
Option Explicit
Dim port, sh, oHttp, webDir, pyw
port = 8123

' --- 1) probe port: if the dashboard answers, assume workbench is already up ---
On Error Resume Next
Set oHttp = CreateObject("MSXML2.XMLHTTP")
oHttp.Open "GET", "http://127.0.0.1:" & port & "/api/dashboard", False
oHttp.Send
If Err.Number = 0 Then
    If oHttp.Status = 200 Then WScript.Quit 0
End If
On Error Goto 0

' --- 2) not up: resolve paths via environment (no hardcoded Chinese) ---
Set sh = CreateObject("WScript.Shell")
webDir = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.dsh\papers\workbench\web"
pyw = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe"

Dim fso
Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FileExists(pyw) Then pyw = "pythonw"

sh.CurrentDirectory = webDir
sh.Run """" & pyw & """ server.py " & port, 0, False
WScript.Quit 0
