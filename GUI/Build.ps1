#Requires -Version 5.1
# Build with the Windows .NET Framework compiler; no package downloads required.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
if (-not (Test-Path (Join-Path $framework 'csc.exe'))) { $framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319' }
$references = @('System.dll', 'System.Core.dll', 'System.Xml.dll', 'System.Xaml.dll', 'System.Web.Extensions.dll', 'System.Windows.Forms.dll')
$arguments = @('/nologo', '/target:winexe', '/optimize+', ('/out:' + (Join-Path $root 'Convenify.exe')))
foreach ($reference in $references) { $arguments += '/reference:' + (Join-Path $framework $reference) }
foreach ($reference in @('PresentationFramework.dll', 'PresentationCore.dll', 'WindowsBase.dll')) { $arguments += '/reference:' + (Join-Path $framework ('WPF\' + $reference)) }
$arguments += @((Join-Path $PSScriptRoot 'Program.cs'), (Join-Path $PSScriptRoot 'App.cs'), (Join-Path $PSScriptRoot 'ProcessRunner.cs'), (Join-Path $PSScriptRoot 'Themes.cs'), (Join-Path $PSScriptRoot 'OptionHelp.cs'))
$arguments += (Join-Path $PSScriptRoot 'ConsoleLogBuffer.cs')
$arguments += (Join-Path $PSScriptRoot 'AppDefaults.cs'), (Join-Path $PSScriptRoot 'SettingsRegistry.cs')
& (Join-Path $framework 'csc.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Convenify desktop build failed.' }
Write-Output ('Built ' + (Join-Path $root 'Convenify.exe'))
