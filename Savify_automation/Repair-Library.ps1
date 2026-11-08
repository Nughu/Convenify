#Requires -Version 5.1
<#
Run through ..\Repair-Library.bat, or invoke this script in PowerShell.
Use ..\Repair-Library-NML.bat or -NmlMode to select an exported Traktor NML file.
The supplied Python installation must already contain Savify and its dependencies.
#>
[CmdletBinding()]
param(
    [switch]$NmlMode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# Preserve native exit codes when this script is also used with PowerShell 7.
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Read-YesNo {
    param([string]$Prompt)
    while ($true) {
        $answer = (Read-Host "$Prompt [y/N]").Trim()
        if ($answer -match '^(?i:y|yes)$') { return $true }
        if ($answer -match '^(?i:n|no)?$') { return $false }
        Write-Host 'Please enter Y or N.'
    }
}

function Resolve-UserPath {
    param([string]$Text)
    $value = $Text.Trim()
    # Accept paths pasted with matching surrounding quotes.
    if ($value.Length -ge 2) {
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
    }
    if ([string]::IsNullOrWhiteSpace($value)) { throw 'Enter a path.' }
    $value = [Environment]::ExpandEnvironmentVariables($value)
    $resolved = Resolve-Path -LiteralPath $value -ErrorAction Stop
    if ($resolved.Provider.Name -ne 'FileSystem') {
        throw 'Enter a filesystem path.'
    }
    $path = $resolved.ProviderPath
    # Avoid a quoted trailing backslash in Windows PowerShell's native argument
    # handling. Keep drive roots (C:\) intact; UNC share roots need no final slash.
    if ($path -notmatch '^[A-Za-z]:\\$') {
        $path = $path.TrimEnd([char[]]'\/')
    }
    return $path
}

function Get-ProgramVersion {
    param([string]$Executable)
    # Redirect both streams without PowerShell 5.1 treating native stderr as
    # a terminating PowerShell error. Drain them concurrently to avoid blocking.
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = '-version'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw "Could not start: $Executable" }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(30000)) {
            $process.Kill()
            throw "Version check timed out: $Executable"
        }
        $output = $stdout.Result + "`n" + $stderr.Result
        if ($process.ExitCode -ne 0) {
            throw "Version check failed for $Executable (exit code $($process.ExitCode))."
        }
        return $output
    }
    finally {
        $process.Dispose()
    }
}

function Get-FFmpegDirectory {
    param([string]$Path)
    $resolved = Resolve-UserPath -Text $Path
    if (Test-Path -LiteralPath $resolved -PathType Leaf) {
        if ([IO.Path]::GetFileName($resolved) -ine 'ffmpeg.exe') {
            throw 'Select ffmpeg.exe, its bin folder, or the extracted build folder.'
        }
        $directory = Split-Path -Parent $resolved
    }
    elseif (Test-Path -LiteralPath (Join-Path $resolved 'ffmpeg.exe') -PathType Leaf) {
        $directory = $resolved
    }
    else {
        $directory = Join-Path $resolved 'bin'
    }
    $ffmpeg = Join-Path $directory 'ffmpeg.exe'
    $ffprobe = Join-Path $directory 'ffprobe.exe'
    if (-not (Test-Path -LiteralPath $ffmpeg -PathType Leaf) -or
        -not (Test-Path -LiteralPath $ffprobe -PathType Leaf)) {
        throw 'The selected folder must contain ffmpeg.exe and ffprobe.exe, directly or in bin.'
    }
    $version = Get-ProgramVersion -Executable $ffmpeg
    if ($version -notmatch '--enable-chromaprint\b') {
        throw 'This FFmpeg lacks Chromaprint support. Select a full build, or choose the download option.'
    }
    $null = Get-ProgramVersion -Executable $ffprobe
    return $directory
}

function Save-Download {
    param([string]$Uri, [string]$Destination)
    $partial = "$Destination.part"
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $partial -UseBasicParsing -TimeoutSec 1800
        if ((Get-Item -LiteralPath $partial).Length -eq 0) {
            throw 'The download was empty.'
        }
        Move-Item -LiteralPath $partial -Destination $Destination -Force
    }
    finally {
        if (Test-Path -LiteralPath $partial) {
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        }
    }
}

function Install-FullFFmpeg {
    param([string]$ToolsDirectory)
    $installation = Join-Path $ToolsDirectory 'ffmpeg-full'
    if (Test-Path -LiteralPath $installation) {
        try {
            $cached = Get-FFmpegDirectory -Path $installation
            Write-Host "Using the downloaded full FFmpeg build: $cached"
            return $cached
        }
        catch {
            Write-Warning "The cached FFmpeg build could not be used: $($_.Exception.Message)"
            Write-Host 'Downloading a replacement.'
        }
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'The automatic full FFmpeg download requires 64-bit Windows.'
    }

    $null = [IO.Directory]::CreateDirectory($ToolsDirectory)
    $staging = Join-Path $ToolsDirectory ('download-' + [guid]::NewGuid().ToString('N'))
    $null = [IO.Directory]::CreateDirectory($staging)
    $oldTls = [Net.ServicePointManager]::SecurityProtocol
    try {
        [Net.ServicePointManager]::SecurityProtocol = $oldTls -bor [Net.SecurityProtocolType]::Tls12
        # Deliberately use the FULL build. The essentials ZIP lacks Chromaprint.
        # Published here: https://www.gyan.dev/ffmpeg/builds/
        $archiveUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full.7z'
        $archive = Join-Path $staging 'ffmpeg-release-full.7z'
        $checksumFile = Join-Path $staging 'ffmpeg-release-full.7z.sha256'
        Write-Host 'Downloading the full FFmpeg build. This may take several minutes...'
        Save-Download -Uri $archiveUrl -Destination $archive
        Save-Download -Uri "$archiveUrl.sha256" -Destination $checksumFile
        $checksumText = Get-Content -LiteralPath $checksumFile -Raw
        $hashMatch = [regex]::Match($checksumText, '(?im)^\s*([0-9a-f]{64})(?:\s|$)')
        if (-not $hashMatch.Success) { throw 'The FFmpeg checksum file was not valid.' }
        $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
        if ($actualHash -ine $hashMatch.Groups[1].Value) {
            throw 'FFmpeg SHA-256 verification failed. Run the launcher again to download a fresh copy.'
        }
        Write-Host 'FFmpeg SHA-256 verified.'

        # Portable official 7-Zip extractor; no installation or administrator rights.
        # The latest official release includes the standalone asset 7zr.exe.
        $extractor = Join-Path $staging '7zr.exe'
        Write-Host 'Downloading the portable 7-Zip extractor...'
        Save-Download -Uri 'https://github.com/ip7z/7zip/releases/latest/download/7zr.exe' -Destination $extractor
        $unpacked = Join-Path $staging 'unpacked'
        $null = [IO.Directory]::CreateDirectory($unpacked)
        Write-Host 'Extracting the full FFmpeg build...'
        & $extractor x $archive "-o$unpacked" -y | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "7-Zip extraction failed (exit code $LASTEXITCODE)." }

        $candidates = @(Get-ChildItem -LiteralPath $unpacked -Directory | Where-Object {
            Test-Path -LiteralPath (Join-Path $_.FullName 'bin\ffmpeg.exe') -PathType Leaf
        })
        if ($candidates.Count -ne 1) { throw 'The FFmpeg archive had an unexpected folder layout.' }
        $buildDirectory = $candidates[0].FullName
        $null = Get-FFmpegDirectory -Path $buildDirectory
        # Replace only this launcher's managed cache, after validating the new build.
        if (Test-Path -LiteralPath $installation) {
            Remove-Item -LiteralPath $installation -Recurse -Force
        }
        Move-Item -LiteralPath $buildDirectory -Destination $installation
        return (Get-FFmpegDirectory -Path $installation)
    }
    finally {
        [Net.ServicePointManager]::SecurityProtocol = $oldTls
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Set-AcoustIdKey {
    $existing = [Environment]::GetEnvironmentVariable('ACOUSTID_API_KEY', 'User')
    if ([string]::IsNullOrWhiteSpace($existing)) {
        $existing = $env:ACOUSTID_API_KEY
    }
    Write-Host ''
    Write-Host 'Enter your AcoustID application API key (not your personal user API key).'
    Write-Host 'Application keys: https://acoustid.org/my-applications'
    if (-not [string]::IsNullOrWhiteSpace($existing)) {
        Write-Host 'A key is already configured. Press Enter to keep it, or enter a replacement.'
    }
    while ($true) {
        $secret = Read-Host 'AcoustID application API key (input hidden)' -AsSecureString
        $pointer = [IntPtr]::Zero
        try {
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
            $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer).Trim()
        }
        finally {
            if ($pointer -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            }
            $secret.Dispose()
        }
        if ([string]::IsNullOrWhiteSpace($key) -and
            -not [string]::IsNullOrWhiteSpace($existing)) {
            $key = $existing.Trim()
        }
        if ([string]::IsNullOrWhiteSpace($key) -or $key -match '\s') {
            Write-Host 'Enter a non-empty application API key without whitespace.'
            continue
        }
        # Persist for this Windows user AND update this process so Python sees it now.
        [Environment]::SetEnvironmentVariable('ACOUSTID_API_KEY', $key, 'User')
        $env:ACOUSTID_API_KEY = $key
        Write-Host 'ACOUSTID_API_KEY saved for your Windows user and set for this run.'
        return
    }
}

$exitCode = 1
$locationPushed = $false
$originalPath = $env:PATH
try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This launcher requires Windows.'
    }
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $python = Join-Path $projectRoot 'python\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python was not found at: $python"
    }
    # Match the original command's working directory, regardless of how launched.
    Push-Location -LiteralPath $projectRoot
    $locationPushed = $true
    if ($NmlMode) {
        Write-Host 'Savify NML playlist repair' -ForegroundColor Cyan
    }
    else {
        Write-Host 'Savify library repair' -ForegroundColor Cyan
    }
    Write-Host "Python: $python"
    Write-Host 'Relative paths are resolved from the folder containing Repair-Library.bat.'
    Write-Host ''

    $nmlPath = $null
    if ($NmlMode) {
        Write-Host 'Select an NML export that references the original music files.'
        while ($true) {
            try {
                $nmlPath = Resolve-UserPath -Text (Read-Host 'Path to the exported Traktor NML file')
                if (-not (Test-Path -LiteralPath $nmlPath -PathType Leaf)) {
                    throw 'The NML path must be an existing file.'
                }
                if ([IO.Path]::GetExtension($nmlPath) -ine '.nml') {
                    throw 'Select a Traktor playlist export with the .nml extension.'
                }
                break
            }
            catch { Write-Warning $_.Exception.Message }
        }
        Write-Host 'The music library folder must contain all tracks listed in this NML export.'
        Write-Host ''
    }

    while ($true) {
        try {
            $library = Resolve-UserPath -Text (Read-Host 'Path to your music library')
            if (-not (Test-Path -LiteralPath $library -PathType Container)) {
                throw 'The music library path must be an existing directory.'
            }
            break
        }
        catch { Write-Warning $_.Exception.Message }
    }

    while ($true) {
        if (Read-YesNo -Prompt 'Do you have a full FFmpeg build available?') {
            try {
                $ffmpegDirectory = Get-FFmpegDirectory -Path (Read-Host 'FFmpeg build folder, bin folder, or ffmpeg.exe path')
                break
            }
            catch { Write-Warning $_.Exception.Message }
        }
        else {
            $ffmpegDirectory = Install-FullFFmpeg -ToolsDirectory (Join-Path $PSScriptRoot 'tools')
            break
        }
    }

    Set-AcoustIdKey
    # Keep subprocesses using the same FFmpeg; do not change the saved user PATH.
    $env:PATH = $ffmpegDirectory + [IO.Path]::PathSeparator + $originalPath
    $auditArguments = @(
        '-m', 'savify.library_audit', $library,
        '--repair', '--spotify',
        '--ffmpeg-location', $ffmpegDirectory,
        '--duration-tolerance', '5',
        '--min-duration', '60',
        '--max-duration', '600'
    )
    if ($NmlMode) {
        $auditArguments += @('--nml', $nmlPath)
    }
    Write-Host ''
    Write-Host "Library: $library"
    Write-Host "FFmpeg:  $ffmpegDirectory"
    Write-Host ('Options: ' + ($auditArguments[3..($auditArguments.Count - 1)] -join ' '))
    if ($NmlMode) {
        Write-Host "NML:     $nmlPath"
        Write-Host 'Repair can replace only the NML-listed tracks in the selected music library.' -ForegroundColor Yellow
    }
    else {
        Write-Host 'Repair can replace files in the selected music library.' -ForegroundColor Yellow
    }
    $confirmed = (Read-Host 'Have you backed up this library? Type YES to start repair; anything else cancels').Trim()
    if ($confirmed -ine 'YES') {
        Write-Host 'Cancelled. No library repair was started.'
        $exitCode = 0
    }
    else {
        Write-Host ''
        Write-Host 'Starting Savify library repair...' -ForegroundColor Cyan
        # Use the call operator and an argument array; do not build a command string.
        & $python @auditArguments
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) {
            Write-Host 'Savify finished successfully.' -ForegroundColor Green
        }
        else {
            Write-Host "Savify exited with code $exitCode. Check its output above." -ForegroundColor Red
        }
    }
}
catch {
    Write-Host ''
    Write-Host ("Error: " + $_.Exception.Message) -ForegroundColor Red
    $exitCode = 1
}
finally {
    $env:PATH = $originalPath
    if ($locationPushed) { Pop-Location }
}
exit $exitCode
