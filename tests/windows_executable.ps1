param([string]$Channel = 'stable', [string]$Dist = 'dist')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
$product = if ($Channel -eq 'dev') { 'Bactologix-Dev' } elseif ($Channel -eq 'stable') { 'Bactologix' } else { throw 'Invalid setup channel' }
$exe = (Resolve-Path (Join-Path $Dist ($product + '-Setup.exe'))).Path
$receipt = Get-Content -Raw -LiteralPath (Join-Path $Dist ($product + '-Setup.build.json')) | ConvertFrom-Json
$parent = Join-Path $env:TEMP ('bactologix-exe-check-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $parent | Out-Null
$destination = Join-Path $parent ('verified payload ' + [char]0x03A9 + ' with spaces')
function Verify-Payload([string]$Executable, [string]$Folder) {
    $info = [Diagnostics.ProcessStartInfo]::new($Executable)
    $info.Arguments = '--verify-payload "' + $Folder + '"'
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $task = [Diagnostics.Process]::Start($info)
    $stdout = $task.StandardOutput.ReadToEndAsync(); $stderr = $task.StandardError.ReadToEndAsync()
    if (-not $task.WaitForExit(30000)) { $task.Kill(); throw 'Payload verification timed out' }
    return @{Code=$task.ExitCode; Output=$stdout.Result; Error=$stderr.Result}
}
try {
    $result = Verify-Payload $exe $destination
    Assert ($result.Code -eq 0) ('Native verification failed: ' + $result.Error)
    $manifest = Get-Content -Raw -LiteralPath (Join-Path $destination 'verified-payload.json') | ConvertFrom-Json
    Assert ($manifest.channel -eq $Channel -and $manifest.product -eq $product) 'Embedded setup channel differs'
    Assert ($manifest.setup_version -eq $receipt.setup_version -and $manifest.source_commit -eq $receipt.source_commit) 'Embedded setup identity differs'
    foreach ($file in $manifest.files) {
        $path = Join-Path $destination $file.name
        Assert ((Get-Item -LiteralPath $path).Length -eq $file.size) ('Extracted size mismatch: ' + $file.name)
        Assert ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -eq $file.sha256) ('Extracted hash mismatch: ' + $file.name)
    }
    Assert (-not (Test-Path -LiteralPath (Join-Path $destination 'installation.json'))) 'Verification mode started installation'
    if ($Channel -eq 'dev') {
        $pinned = Get-Content -Raw -LiteralPath (Join-Path $destination 'dev-package.json') | ConvertFrom-Json
        Assert ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $destination $pinned.wheel)).Hash.ToLowerInvariant() -eq $pinned.sha256) 'Dev pinned wheel differs'
        Assert ((Get-Content -Raw -LiteralPath (Join-Path $destination ($product + '.ps1'))) -match 'bactologix.dev_cli') 'Dev executable lost its isolated launcher'
    }
    # Reflection checks the actual compiled command without launching setup or WSL.
    $assembly = [Reflection.Assembly]::LoadFile($exe)
    $flags = [Reflection.BindingFlags]'Static,NonPublic'
    $command = $assembly.GetType('SetupBootstrap').GetMethod('SetupCommand', $flags).Invoke($null, @($destination))
    Assert ($command.FileName -match 'WindowsPowerShell\\v1\.0\\powershell\.exe$') 'Bootstrap uses a non-system shell'
    Assert (-not $command.UseShellExecute -and $command.CreateNoWindow) 'Bootstrap unexpectedly elevates or opens a console'
    Assert ($command.Arguments -match '-STA' -and $command.Arguments -match '-NoProfile' -and $command.Arguments.EndsWith(' -Setup')) 'Bootstrap lost its reviewed setup invocation'
    Assert ($command.Arguments.Contains($product + '.ps1')) 'Bootstrap invokes the wrong channel script'
    $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($exe)
    Assert ($version.ProductVersion -eq $manifest.setup_version -and $version.ProductName -eq $product) 'Windows executable version information differs'
    $icon = [Drawing.Icon]::ExtractAssociatedIcon($exe)
    Assert ($null -ne $icon) 'Windows setup icon is missing'
    $icon.Dispose()
    $sentinel = Join-Path $destination 'keep-existing.txt'
    [IO.File]::WriteAllText($sentinel, 'retained test evidence')
    $again = Verify-Payload $exe $destination
    Assert ($again.Code -ne 0 -and [IO.File]::ReadAllText($sentinel) -eq 'retained test evidence') 'Existing extraction destination was overwritten'
    # Corrupt one embedded ZIP byte, retaining a valid PE: verification must fail
    # before creating a destination, with no interactive error dialog or setup.
    $corrupt = Join-Path $parent 'changed-payload.exe'
    $zip = (Resolve-Path (Join-Path $Dist ($product + '-Windows.zip'))).Path
    $env:BACTOLOGIX_TEST_EXE = $exe; $env:BACTOLOGIX_TEST_ZIP = $zip; $env:BACTOLOGIX_TEST_CHANGED = $corrupt
    python -c "import os,pathlib; p=bytearray(pathlib.Path(os.environ['BACTOLOGIX_TEST_EXE']).read_bytes()); z=pathlib.Path(os.environ['BACTOLOGIX_TEST_ZIP']).read_bytes(); n=p.find(z); assert n>=0; p[n+len(z)//2]^=1; pathlib.Path(os.environ['BACTOLOGIX_TEST_CHANGED']).write_bytes(p)"
    Assert ($LASTEXITCODE -eq 0) 'Could not create corrupted-payload fixture'
    $badDestination = Join-Path $parent 'must-not-exist'
    $bad = Verify-Payload $corrupt $badDestination
    Assert ($bad.Code -ne 0 -and -not (Test-Path -LiteralPath $badDestination)) 'Altered embedded payload was accepted'
    Write-Output ('Native ' + $product + ' executable checks passed: identity, hashes, Unicode paths, command, icon, no-overwrite, tamper rejection; no WSL installation or analysis ran.')
} finally {
    Remove-Item Env:BACTOLOGIX_TEST_EXE,Env:BACTOLOGIX_TEST_ZIP,Env:BACTOLOGIX_TEST_CHANGED -ErrorAction SilentlyContinue
    # Only the unique disposable fixture owned by this test is removed.
    Remove-Item -LiteralPath $parent -Recurse -Force -ErrorAction SilentlyContinue
}
