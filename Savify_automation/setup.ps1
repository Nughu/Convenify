$ErrorActionPreference = 'Stop'

function Read-RequiredSecret {
    param([string]$Prompt)

    while ($true) {
        $secureValue = Read-Host -Prompt $Prompt -AsSecureString
        if ($secureValue.Length -gt 0) {
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
            try {
                return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
            }
            finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            }
        }

        Write-Host 'This value cannot be empty.' -ForegroundColor Yellow
    }
}

Write-Host 'Convenify setup' -ForegroundColor Cyan
$rootDir = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $rootDir 'config.json'
try {
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
}
catch {
    Write-Host "Could not read config.json: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

$libraryPath = (Read-Host 'Music library folder path').Trim().Trim('"')
while ([string]::IsNullOrWhiteSpace($libraryPath)) {
    Write-Host 'The library path cannot be empty.' -ForegroundColor Yellow
    $libraryPath = (Read-Host 'Music library folder path').Trim().Trim('"')
}

$clientId = Read-RequiredSecret 'Spotify client ID'
$clientSecret = Read-RequiredSecret 'Spotify client secret'

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
}
else {
    Write-Host 'Deno is already installed.'
}

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

[Environment]::SetEnvironmentVariable('SPOTIPY_CLIENT_ID', $clientId, 'User')
[Environment]::SetEnvironmentVariable('SPOTIPY_CLIENT_SECRET', $clientSecret, 'User')
$env:SPOTIPY_CLIENT_ID = $clientId
$env:SPOTIPY_CLIENT_SECRET = $clientSecret

Write-Host 'Spotify credentials saved as user environment variables.' -ForegroundColor Green
Write-Host 'Music library path saved to config.json.' -ForegroundColor Green
Write-Host 'Setup complete. Restart any open terminals or VS Code windows before running Convenify.' -ForegroundColor Green