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
            # Readers see one complete sample. Sharing failures are retried on
            # the next sample; the consumer rejects a stale previous reading.
            $null = [BactologixDiskCapacity]::MoveFileEx($temporary, $path, 1)
        } catch [IO.IOException] {
            # Transient share or media errors also leave the last complete record.
        }
        if ($owner.WaitForExit(2000)) { break }
    }
} finally {
    $owner.Dispose()
    if ($temporary) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
}
