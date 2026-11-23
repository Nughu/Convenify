#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Xaml, System.Web.Extensions, System.Windows.Forms
$references = @(
    'System.dll', 'System.Core.dll', 'System.Xml.dll', 'System.Web.Extensions.dll', 'System.Windows.Forms.dll', 'System.Xaml.dll',
    [System.Windows.Window].Assembly.Location,
    [System.Windows.Media.Brush].Assembly.Location,
    [System.Windows.Threading.Dispatcher].Assembly.Location
)
Add-Type -Path (Join-Path $root 'GUI\ProcessRunner.cs'), (Join-Path $root 'GUI\App.cs'), (Join-Path $root 'GUI\Themes.cs'), (Join-Path $root 'GUI\OptionHelp.cs'), (Join-Path $root 'GUI\ConsoleLogBuffer.cs'), (Join-Path $PSScriptRoot 'DesktopPersistenceTests.cs') -ReferencedAssemblies $references
[DesktopPersistenceTests]::Run($root)
