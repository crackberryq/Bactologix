param(
    [Parameter(Mandatory=$true)][string]$Distribution,
    [Parameter(Mandatory=$true)][string]$Session,
    [Parameter(Mandatory=$true)][string]$Directory,
    [Parameter(Mandatory=$true)][int]$OwnerId,
    [Parameter(Mandatory=$true)][long]$OwnerStartTicks
)
$ErrorActionPreference = 'Stop'
$owner = [Diagnostics.Process]::GetProcessById($OwnerId)
try {
    if ($owner.StartTime.ToUniversalTime().Ticks -ne $OwnerStartTicks) { return }
    . (Join-Path $PSScriptRoot 'windows_capacity.ps1')
    $utf8 = New-Object Text.UTF8Encoding($false)
    $path = Join-Path $Directory 'windows-storage.json'
    $temporary = $path + '.' + $PID + '.writing'
    while (-not $owner.HasExited) {
        $cycle = [Diagnostics.Stopwatch]::StartNew()
        $started = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
        try {
            $sample = Get-WindowsStorageCapacity @{operation='discover'; distribution=$Distribution}
        } catch {
            $sample = @{volumes=@(); errors=@($_.Exception.Message); swap_required=$null}
        }
        $sample.schema = 1
        $sample.session = $Session
        $sample.distribution = $Distribution
        $sample.time = $started
        if ($owner.HasExited) { break }
        try {
            [IO.File]::WriteAllText($temporary, ($sample | ConvertTo-Json -Depth 6 -Compress), $utf8)
            # A reader without delete sharing can briefly prevent replacement.
            # Retry the same complete observation without advancing its timestamp.
            $retry = [Diagnostics.Stopwatch]::StartNew()
            while (-not [BactologixDiskCapacity]::MoveFileEx($temporary, $path, 1)) {
                $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
                if ($errorCode -notin @(5, 32, 33) -or $retry.ElapsedMilliseconds -ge 1000) { break }
                if ($owner.WaitForExit(50)) { break }
            }
        } catch [IO.IOException] {
            # Transient share or media errors also leave the last complete record.
        }
        $remaining = [Math]::Max([long]0, 2000 - $cycle.ElapsedMilliseconds)
        if ($owner.WaitForExit([int]$remaining)) { break }
    }
} finally {
    $owner.Dispose()
    if ($temporary) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
}
