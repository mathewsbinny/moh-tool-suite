@echo off
rem Builds DbxEdit.exe (command line) and DbxEditGUI.exe (window) next to this file with the .NET Framework 4
rem C# compiler that ships with Windows.
setlocal
set HERE=%~dp0
set CSC=C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe
if not exist "%CSC%" (echo C# compiler not found: %CSC% & exit /b 1)
"%CSC%" -nologo -optimize -out:"%HERE%DbxEdit.exe" "%HERE%DbxEdit.cs" || exit /b 1
"%CSC%" -nologo -optimize -target:winexe -main:MohDbx.Gui.GuiProgram -out:"%HERE%DbxEditGUI.exe" ^
    -r:System.Windows.Forms.dll -r:System.Drawing.dll "%HERE%DbxEditGUI.cs" "%HERE%DbxEdit.cs" || exit /b 1
echo Built DbxEdit.exe and DbxEditGUI.exe
