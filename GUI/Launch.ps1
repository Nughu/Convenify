#Requires -Version 5.1
[CmdletBinding()]
param([switch]$Check, [switch]$SmokeTest)
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Xaml, System.Web.Extensions, System.Windows.Forms
    $references = @(
        'System.dll', 'System.Core.dll', 'System.Xml.dll', 'System.Web.Extensions.dll', 'System.Windows.Forms.dll', 'System.Xaml.dll',
        [System.Windows.Window].Assembly.Location,
        [System.Windows.Media.Brush].Assembly.Location,
        [System.Windows.Threading.Dispatcher].Assembly.Location
    )
    Add-Type -Path (Join-Path $PSScriptRoot 'ProcessRunner.cs'), (Join-Path $PSScriptRoot 'App.cs'), (Join-Path $PSScriptRoot 'Themes.cs'), (Join-Path $PSScriptRoot 'OptionHelp.cs'), (Join-Path $PSScriptRoot 'ConsoleLogBuffer.cs') -ReferencedAssemblies $references
    if ($Check) { Write-Output 'Desktop sources compiled successfully.'; exit 0 }
    if ($SmokeTest) { [Convenify.Desktop.DesktopApp]::SmokeTest((Split-Path -Parent $PSScriptRoot)); exit 0 }
    [Convenify.Desktop.DesktopApp]::Run((Split-Path -Parent $PSScriptRoot))
}
catch {
    if ($Check -or $SmokeTest) { Write-Error $_; exit 1 }
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("Convenify could not start.`n`n$($_.Exception.Message)`n`nKeep the GUI, python and savify-new folders together.", 'Convenify', 'OK', 'Error') | Out-Null
    exit 1
}
