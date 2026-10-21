' Launch the local web server hidden (no console window).
' Called by the Startup shortcut so the app survives reboots / closing windows.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
sh.Run """" & base & "\run_server.bat""", 0, False
