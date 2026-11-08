@echo off
setlocal DisableDelayedExpansion

rem This file belongs beside the python and Savify_automation folders.
set "repair_script=%~dp0Savify_automation\Repair-Library.ps1"
set "powershell_exe=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

if not exist "%repair_script%" (
    echo ERROR: PowerShell script not found:
    echo "%repair_script%"
    pause
    exit /b 1
)
if not exist "%powershell_exe%" (
    echo ERROR: Windows PowerShell was not found.
    pause
    exit /b 1
)

rem The execution-policy override applies only to this PowerShell process.
rem The PowerShell script resolves Python and its working directory itself.
"%powershell_exe%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%repair_script%"
set "repair_exit_code=%ERRORLEVEL%"

echo.
echo Launcher exit code: %repair_exit_code%
pause
exit /b %repair_exit_code%
