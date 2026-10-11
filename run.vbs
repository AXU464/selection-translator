' Launch 划词翻译 without a console window (used by the Startup shortcut).
Option Explicit

Dim fso, shell, root, target, pythonw, python
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)
target = fso.BuildPath(root, "main.py")

pythonw = "pythonw.exe"
python = "python.exe"

shell.CurrentDirectory = root
On Error Resume Next
shell.Run """" & pythonw & """ """ & target & """", 0, False
If Err.Number <> 0 Then
    Err.Clear
    shell.Run """" & python & """ """ & target & """", 0, False
End If
On Error GoTo 0
