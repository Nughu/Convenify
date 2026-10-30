$ErrorActionPreference = 'Stop'

function Read-RequiredSecret {
    param([string]$Prompt)

    while ($true) {
        $secureValue = Read-Host -Prompt $Prompt -AsSecureString
        if ($secureValue.Length -gt 0) {
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
            try {
                $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
                if (-not [string]::IsNullOrWhiteSpace($value)) {
                    return $value
                }
            }
            finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            }
        }

        Write-Host 'This value cannot be empty.' -ForegroundColor Yellow
    }
}

function Get-ConfiguredEnvironmentVariable {
    param([string]$Name)

    # A terminal may not yet have inherited variables saved by an earlier setup.
    foreach ($target in @('Process', 'User', 'Machine')) {
        $value = [Environment]::GetEnvironmentVariable($Name, $target)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value
        }
    }

    return $null
}

Write-Host 'Convenify setup' -ForegroundColor Cyan
$rootDir = $env:CONVENIFY_ROOT_DIR
if ([string]::IsNullOrWhiteSpace($rootDir)) {
    $rootDir = Split-Path -Parent $PSScriptRoot
}
$configPath = Join-Path $rootDir 'config.json'
try {
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
}
catch {
    Write-Host "Could not read config.json: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

$libraryPath = [string]$config.library_path
$needsLibraryPath = [string]::IsNullOrWhiteSpace($libraryPath)
if ($needsLibraryPath) {
    $libraryPath = (Read-Host 'Music library folder path').Trim().Trim('"')
    while ([string]::IsNullOrWhiteSpace($libraryPath)) {
        Write-Host 'The library path cannot be empty.' -ForegroundColor Yellow
        $libraryPath = (Read-Host 'Music library folder path').Trim().Trim('"')
    }
}

$credentials = @(
    @{ Name = 'SPOTIPY_CLIENT_ID'; Prompt = 'Spotify client ID' },
    @{ Name = 'SPOTIPY_CLIENT_SECRET'; Prompt = 'Spotify client secret' }
)
foreach ($credential in $credentials) {
    $credential.Value = Get-ConfiguredEnvironmentVariable $credential.Name
    $credential.Missing = [string]::IsNullOrWhiteSpace($credential.Value)
    if ($credential.Missing) {
        $credential.Value = Read-RequiredSecret $credential.Prompt
    }
}

$setupChanged = $false
$restartRequired = $false

if (-not (Get-Command deno -ErrorAction SilentlyContinue)) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Error 'winget was not found. Install or update App Installer, then run setup again.'
        exit 1
    }

    Write-Host 'Installing Deno with winget...'
    & $winget.Source install --id DenoLand.Deno --exact --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Deno installation failed with exit code $LASTEXITCODE. Resolve the issue and run setup again."
        exit $LASTEXITCODE
    }

    $setupChanged = $true
    $restartRequired = $true
}

if ($needsLibraryPath) {
    New-Item -ItemType Directory -Path $libraryPath -Force | Out-Null
    if ($config.PSObject.Properties.Name -contains 'library_path') {
        $config.library_path = $libraryPath
    }
    else {
        $config | Add-Member -MemberType NoteProperty -Name 'library_path' -Value $libraryPath
    }
    [System.IO.File]::WriteAllText(
        $configPath,
        ($config | ConvertTo-Json -Depth 100),
        [System.Text.UTF8Encoding]::new($false)
    )

    $setupChanged = $true
    Write-Host 'Music library path saved to config.json.' -ForegroundColor Green
}

foreach ($credential in $credentials) {
    if ($credential.Missing) {
        [Environment]::SetEnvironmentVariable($credential.Name, $credential.Value, 'User')
        $setupChanged = $true
        $restartRequired = $true
        Write-Host "$($credential.Prompt) saved as a user environment variable." -ForegroundColor Green
    }

    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($credential.Name, 'Process'))) {
        [Environment]::SetEnvironmentVariable($credential.Name, $credential.Value, 'Process')
        $setupChanged = $true
        $restartRequired = $true
    }
}

if ($setupChanged) {
    Write-Host 'Setup complete.' -ForegroundColor Green
}
else {
    Write-Host 'Convenify is already configured.' -ForegroundColor Green
}
if ($restartRequired) {
    Write-Host 'Restart any open terminals or VS Code windows before running Convenify.' -ForegroundColor Green
}