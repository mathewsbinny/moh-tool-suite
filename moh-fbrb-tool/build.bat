@echo off
rem Builds FbrbTool.exe next to this file with the .NET Framework 4 C# compiler (part of Windows).
setlocal
set HERE=%~dp0
set CSC=C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe
if not exist "%CSC%" (echo C# compiler not found: %CSC% & exit /b 1)
"%CSC%" -nologo -optimize -out:"%HERE%FbrbTool.exe" "%HERE%FbrbTool.cs" || exit /b 1
echo Built FbrbTool.exe
