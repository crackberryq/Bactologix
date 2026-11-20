$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$temp = Join-Path $env:TEMP ('bactologix-dev-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $temp | Out-Null
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
try {
    $wheel = Join-Path $temp 'bactologix-0.1.0a86.dev5-py3-none-any.whl'
    python -c "import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],'w'); z.writestr('bactologix-0.1.0a86.dev5.dist-info/METADATA','Name: bactologix\nVersion: 0.1.0a86.dev5\n'); z.close()" $wheel
    if ($LASTEXITCODE -ne 0) { throw 'Fixture build failed' }
    $archive = Join-Path $temp 'Dev.zip'
    python (Join-Path $repo 'scripts/build_development_bundle.py') --wheel $wheel --output $archive
    if ($LASTEXITCODE -ne 0) { throw 'Dev bundle build failed' }
    Expand-Archive $archive (Join-Path $temp 'unpacked')
    $payload = Join-Path $temp 'unpacked/Bactologix-Dev'
    . (Join-Path $payload 'Bactologix-Dev.ps1') -TestMode
    Assert ($script:Root -eq (Join-Path $env:LOCALAPPDATA 'Bactologix-Dev')) 'Dev desktop root overlaps stable'
    $script:Root = Join-Path $temp 'installed'
    Save-SetupFiles
    Assert (Test-Path (Join-Path $script:Root 'dev-package.json')) 'Dev manifest not copied'
    Assert ((Get-FileHash (Join-Path $script:Root 'windows_capacity.ps1')).Hash -eq
        (Get-FileHash (Join-Path $repo 'src/bactologix/data/windows_capacity.ps1')).Hash) 'Dev host probe is missing or changed'
    $storageDir = Join-Path $temp 'storage session'
    New-Item -ItemType Directory $storageDir | Out-Null
    $session = [Guid]::NewGuid().ToString('N')
    $monitor = Start-DesktopStorageMonitor $storageDir $session 'Unregistered storage fixture'
    try {
        $deadline = [Diagnostics.Stopwatch]::StartNew()
        $first = $null
        do {
            if ($monitor.Process.HasExited) { throw ('Native storage helper exited early: ' + $monitor.Err.Result) }
            $report = Read-Json (Join-Path $storageDir 'windows-storage.json')
            if ($report) {
                Assert ($report.schema -eq 1 -and $report.session -ceq $session) 'Storage report is not scoped to this session'
                Assert ($report.distribution -ceq 'Unregistered storage fixture' -and $report.errors.Count -gt 0) 'Unverified distribution should produce an error'
                if ($null -eq $first) { $first = $report.time }
                elseif ($report.time -gt $first) { break }
            }
            Start-Sleep -Milliseconds 100
        } while ($deadline.Elapsed.TotalSeconds -lt 20)
        Assert ($report -and $report.time -gt $first) 'Native storage helper did not refresh its report'
    } finally {
        if (-not $monitor.Process.HasExited) { $monitor.Process.Kill() }
        $monitor.Process.WaitForExit()
        $monitor.Process.Dispose()
    }
    Assert (Test-Path (Join-Path $script:Root (Split-Path $wheel -Leaf))) 'Pinned wheel not copied'
    $shortcut = Join-Path $temp 'Bactologix-Dev.lnk'
    New-Shortcut $script:Root $shortcut
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    Assert ($link.Arguments -match 'Bactologix-Dev.vbs') 'Dev shortcut launches stable'
    Assert (-not (Test-Path (Join-Path $script:Root 'Bactologix.ps1'))) 'Stable launcher unexpectedly included'
    Write-Output 'Development launcher, copied payload and shortcut isolation passed.'
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
