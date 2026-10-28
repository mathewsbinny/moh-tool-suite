@echo off
rem DbxEdit test suite: builds DbxEdit, DbxEditGUI and the tests, then runs them.
rem   run_tests.bat                          unit tests (no game data needed)
rem   run_tests.bat <folder> [...]           + every binary .dbx under the folders, and the fingerprints in
rem                                          fingerprints_795745.txt for the files that are there
rem Set DBX_TESTS_ARGS for more options (e.g. --edit-every 5, --no-gui).
setlocal
set HERE=%~dp0
set CSC=C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe
call "%HERE%..\build.bat" >nul || (echo build failed & exit /b 1)
"%CSC%" -nologo -optimize -main:DbxTests -out:"%HERE%DbxTests.exe" -r:System.Windows.Forms.dll -r:System.Drawing.dll ^
    "%HERE%DbxTests.cs" "%HERE%..\DbxEdit.cs" "%HERE%..\DbxEditGUI.cs" || (echo test build failed & exit /b 1)
copy /y "%HERE%..\DbxEdit.exe" "%HERE%DbxEdit.exe" >nul
if "%~1"=="" (
    "%HERE%DbxTests.exe" %DBX_TESTS_ARGS%
) else (
    "%HERE%DbxTests.exe" --corpus %* --golden "%HERE%fingerprints_795745.txt" %DBX_TESTS_ARGS%
)
exit /b %errorlevel%
