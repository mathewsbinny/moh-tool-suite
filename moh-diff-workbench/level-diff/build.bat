@echo off
rem Builds LevelDiff.exe next to this file with the .NET Framework 4 C# compiler (part of Windows). It reads .dbx
rem files with the suite's DbxEdit code: ..\..\moh-dbx-tools\DbxEdit.cs (or set DBX_TOOLS to that folder).
setlocal
set HERE=%~dp0
if not defined DBX_TOOLS set DBX_TOOLS=%HERE%..\..\moh-dbx-tools
set CSC=C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe
if not exist "%CSC%" (echo C# compiler not found: %CSC% & exit /b 1)
if not exist "%DBX_TOOLS%\DbxEdit.cs" (echo DbxEdit.cs not found in "%DBX_TOOLS%" - it is in the suite's moh-dbx-tools folder; or set DBX_TOOLS & exit /b 1)
"%CSC%" -nologo -optimize -main:LevelDiff -out:"%HERE%LevelDiff.exe" "%HERE%LevelDiff.cs" "%DBX_TOOLS%\DbxEdit.cs" || exit /b 1
echo Built LevelDiff.exe
