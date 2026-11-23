#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Add-Type -Path (Join-Path $root 'GUI\ConsoleLogBuffer.cs'), (Join-Path $PSScriptRoot 'DesktopLogTests.cs')
[DesktopLogTests]::Run()
