#Requires -Version 5.1
# Offline test of the production runner, including descendant cancellation.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Add-Type -Path (Join-Path $root 'GUI\ProcessRunner.cs'), (Join-Path $root 'GUI\AppDefaults.cs') -ReferencedAssemblies 'System.dll', 'System.Core.dll', 'System.Web.Extensions.dll'
$testFolder = Join-Path $root ('gui-data\runner-test-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($testFolder) | Out-Null
$jobPath = Join-Path $testFolder 'job with spaces.json'
$fixture = Join-Path $PSScriptRoot 'runner_fixture.py'
$utf8 = [Text.UTF8Encoding]::new($false)

function Wait-Runner {
    param($Runner, [int]$Timeout = 20)
    $deadline = [DateTime]::UtcNow.AddSeconds($Timeout)
    $lines = [Collections.Generic.List[string]]::new()
    while ([DateTime]::UtcNow -lt $deadline) {
        $message = $null
        while ($Runner.TryRead([ref]$message)) {
            if ($null -ne $message.ExitCode) {
                return @{ Code = $message.ExitCode; Lines = $lines }
            }
            $lines.Add($message.Text)
        }
        Start-Sleep -Milliseconds 20
    }
    throw 'The runner did not finish within the timeout.'
}

$runner = [Convenify.Desktop.ProcessRunner]::new()
try {
    [IO.File]::WriteAllText($jobPath, '{"mode":"exit"}', $utf8)
    $runner.Start($root, $fixture, $jobPath, (Join-Path $testFolder 'exit.log'))
    $result = Wait-Runner $runner
    $unicodeLine = 'Unicode fixture: Gr' + [char]0x00fc + [char]0x00df + 'e ' + [char]0x97f3 + [char]0x697d
    if ($result.Code -ne 7 -or -not ($result.Lines -contains 'stderr fixture') -or -not ($result.Lines -contains $unicodeLine)) { throw 'Exit code or Unicode stdout/stderr was lost.' }
    Write-Output 'PASS exit codes and both output streams'

    [IO.File]::WriteAllText($jobPath, '{"mode":"logger"}', $utf8)
    $beforeFiles = @(Get-ChildItem -LiteralPath $testFolder -Recurse -File).Count
    $runner.Start($root, $fixture, $jobPath, $null)
    $result = Wait-Runner $runner
    if ($result.Code -ne 0 -or -not ($result.Lines | Where-Object { $_.Contains('Savify DEBUG transport fixture') })) { throw 'Savify DEBUG was not delivered without verbose flags or disk logging.' }
    if (@(Get-ChildItem -LiteralPath $testFolder -Recurse -File).Count -ne $beforeFiles) { throw 'An unsaved run wrote log files.' }
    Write-Output 'PASS real Savify DEBUG transport with file logging disabled'

    $runner.Start($root, $fixture, $jobPath, (Join-Path $testFolder 'debug.log'))
    $result = Wait-Runner $runner
    if ($result.Code -ne 0 -or -not [IO.File]::ReadAllText((Join-Path $testFolder 'debug.log')).Contains('Savify DEBUG transport fixture')) { throw 'Saved console log lost Savify DEBUG output.' }
    if (-not (Test-Path (Join-Path $testFolder 'logs')) -or (Test-Path (Join-Path $testFolder 'legacy'))) { throw 'Savify log was not saved with the GUI run files.' }
    Write-Output 'PASS DEBUG console capture and Savify logs in the saved run folder'

    [IO.File]::WriteAllText($jobPath, '{"mode":"flood"}', $utf8)
    $runner.Start($root, $fixture, $jobPath, (Join-Path $testFolder 'flood.log'))
    # Allow the bounded display buffer to fill; the full log must remain intact.
    Start-Sleep -Milliseconds 700
    $result = Wait-Runner $runner
    $saved = [IO.File]::ReadAllText((Join-Path $testFolder 'flood.log'))
    if ($result.Code -ne 0 -or -not $saved.Contains('Output line 9999') -or -not ($result.Lines | Where-Object { $_.StartsWith('@@CONVENIFY@@') })) { throw 'Flood output, structured events or completion was lost.' }
    Write-Output 'PASS bounded output buffer and complete disk log'

    [IO.File]::WriteAllText($jobPath, '{"mode":"cancel"}', $utf8)
    $runner.Start($root, $fixture, $jobPath, (Join-Path $testFolder 'cancel.log'))
    $childId = $null
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not $childId -and [DateTime]::UtcNow -lt $deadline) {
        $message = $null
        while ($runner.TryRead([ref]$message)) {
            if ($message.Text -match '^CHILD_PID=(\d+)$') { $childId = [int]$Matches[1] }
        }
        Start-Sleep -Milliseconds 20
    }
    if (-not $childId) { throw 'Child fixture did not start.' }
    $runner.Cancel()
    $result = Wait-Runner $runner
    if ($result.Code -ne 130) { throw 'Cancelled task did not report stopped.' }
    if (Get-Process -Id $childId -ErrorAction SilentlyContinue) { throw 'Cancelling left a child process alive.' }
    Write-Output 'PASS cancellation terminates worker and child process'

    [IO.File]::WriteAllText($jobPath, '{"mode":"exit"}', $utf8)
    $runner.Start($root, $fixture, $jobPath, (Join-Path $testFolder 'restart.log'))
    $result = Wait-Runner $runner
    if ($result.Code -ne 7) { throw 'Could not start another task after cancellation.' }
    Write-Output 'PASS next task starts after cancellation'
}
finally { $runner.Dispose() }
