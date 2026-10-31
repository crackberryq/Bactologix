$ErrorActionPreference = 'Stop'
$VerbosePreference = 'Continue'
$repo = Split-Path $PSScriptRoot -Parent
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
$temporary = Join-Path $env:TEMP ('bactologix-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $temporary | Out-Null
python (Join-Path $repo 'scripts/build_windows_bundle.py')
Expand-Archive -LiteralPath (Join-Path $repo 'dist/Bactologix-Windows.zip') -DestinationPath $temporary
. (Join-Path $temporary 'Bactologix/Bactologix.ps1') -TestMode
$views = @(); $profiles = @(); $browserProcesses = @()
try {
    $listing = (@('--list','--quiet') | ForEach-Object { Quote-Argument $_ }) -join ' '
    Assert ($listing -eq '--list --quiet') 'WSL switches must not be quoted into Linux commands'
    $execution = (@('--distribution','Ubuntu-24.04','--exec','id','-u') | ForEach-Object { Quote-Argument $_ }) -join ' '
    Assert ($execution -eq '--distribution Ubuntu-24.04 --exec id -u') 'WSL execution options were quoted'
    # Exercise the Windows WSL executable itself, not just Python's CRT parser.
    # --help does not need a distro and must never be routed into Bash.
    $wslHelp = Start-Owned 'wsl.exe' @('--help') -Unicode
    Assert ($wslHelp.Process.WaitForExit(30000)) 'Native WSL help timed out'
    $null = $wslHelp.Copy.GetAwaiter().GetResult()
    $helpText = Decode-WslOutput ($wslHelp.Bytes.ToArray()); $wslHelp.Bytes.Dispose()
    Assert ($wslHelp.Process.ExitCode -eq 0 -and $helpText -match '--distribution') ('Native WSL option parsing failed: ' + $helpText + $wslHelp.Err.Result)
    Write-Output 'Native wsl.exe option parsing passed.'
    foreach ($encoding in @('utf-8','utf-8-sig','utf-16','utf-16-le','utf-16-be')) {
        $code = 'import sys; sys.stdout.buffer.write(("Ubuntu\\r\\nUbuntu-24.04\\r\\n".replace("\\r",chr(13)).replace("\\n",chr(10))).encode("' + $encoding + '"))'
        if ($encoding -eq 'utf-16-be') { $code = 'import sys; sys.stdout.buffer.write(bytes([254,255])+"Ubuntu\\r\\nUbuntu-24.04\\r\\n".replace("\\r",chr(13)).replace("\\n",chr(10)).encode("utf-16-be"))' }
        $probe = Start-Owned 'python.exe' @('-c',$code) -Unicode
        Assert ($probe.Process.WaitForExit(15000)) 'WSL encoding fixture timed out'
        Assert ($probe.Process.ExitCode -eq 0) $probe.Err.Result
        $null = $probe.Copy.GetAwaiter().GetResult()
        $decoded = Decode-WslOutput ($probe.Bytes.ToArray()); $probe.Bytes.Dispose()
        Assert ($decoded -eq "Ubuntu`r`nUbuntu-24.04") ('WSL names corrupted for ' + $encoding)
    }
    $originalCapture = (Get-Item Function:Capture).ScriptBlock
    try {
        function Capture { return "Ubuntu-24.04`r`nUbuntu`r`n" }
        Assert ((@(Get-InstalledDistributions) -join ',') -eq 'Ubuntu-24.04,Ubuntu') 'Installed distribution names not preserved'
        function Capture { throw 'fixture WSL detection error' }
        $message = ''
        try { Get-InstalledDistributions } catch { $message = $_.Exception.Message }
        Assert ($message -like '*Could not read installed WSL distributions*fixture WSL detection error*') 'Detection failure was treated as missing Ubuntu'
    } finally { Set-Item Function:Capture $originalCapture }
    $scriptPath = Join-Path $repo 'scripts/windows/Bactologix.ps1'
    $tokens=$null; $errors=$null
    $null = [Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$tokens,[ref]$errors)
    Assert ($errors.Count -eq 0) ($errors | Out-String)
    $values = @('--list','--distribution','Ubuntu-24.04','--exec','/home/jimi/miniconda3/bin/python','space and apostrophe''s','double"quote','C:\ends\','a&b;$x', '')
    $task = Start-Owned 'python.exe' (@('-c','import json,sys; print(json.dumps(sys.argv[1:]))') + $values)
    Assert ($task.Process.WaitForExit(15000)) 'Argument echo timed out'
    $actual = $task.Out.Result | ConvertFrom-Json
    Assert (($actual | ConvertTo-Json -Compress) -eq ($values | ConvertTo-Json -Compress)) 'Windows arguments changed'
    Write-Json (Join-Path $temporary 'state.json') @{session='test'; phase='starting'}
    Write-Json (Join-Path $temporary 'state.json') @{session='test'; phase='ready'}
    Assert ((Read-Json (Join-Path $temporary 'state.json')).phase -eq 'ready') 'Atomic control replacement failed'
    $shortcut = Join-Path $temporary 'Bactologix.lnk'
    New-Shortcut $temporary $shortcut
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    Assert ($link.IconLocation -eq ((Join-Path $temporary 'brand.ico') + ',0')) 'Shortcut icon not bound to app brand'
    Assert ($link.TargetPath -like '*wscript.exe') 'Silent shortcut entry missing'
    $script:AllowClose = $true
    $view = New-Progress 'Bactologix setup preview'
    $views += $view
    # Load the exact application asset used in the distribution bundle.
    $view.Form.Icon = New-Object Drawing.Icon((Join-Path $repo 'src/bactologix/ui/brand.ico'))
    Show-Stage $view 'Preparing Bactomics' 'Reusing your existing environment. Your pipelines, databases and completed work stay in place.'
    $view.Steps.Text = "Stage 3 of 5`r`nUbuntu  >  Conda  >  Bactomics  >  Bactologix  >  Desktop shortcut"
    $bitmap = New-Object Drawing.Bitmap($view.Form.Width, $view.Form.Height)
    $view.Form.DrawToBitmap($bitmap, (New-Object Drawing.Rectangle(0,0,$view.Form.Width,$view.Form.Height)))
    $bitmap.Save((Join-Path $repo 'windows-setup-preview.png')); $bitmap.Dispose()
    $view.Form.Hide()
    $browser = Find-Browser
    foreach ($name in @('owned','independent')) {
        $profile = Join-Path $temporary $name; $profiles += $profile
        $task = Start-Owned $browser @('--headless=new',"--user-data-dir=$profile",'--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--no-first-run','--disable-background-mode','about:blank')
        $browserProcesses += $task.Process
        $deadline = [Diagnostics.Stopwatch]::StartNew()
        while ($deadline.Elapsed.TotalSeconds -lt 45 -and -not (Browser-State $profile).reachable) { Start-Sleep -Milliseconds 200 }
        Assert ((Browser-State $profile).pages -ge 1) 'Dedicated browser was not observable'
    }
    Close-OwnedBrowser $profiles[0]
    # Headless Edge runs in this foreground process. Check actual termination:
    # its HTTP endpoint can briefly accept empty responses during teardown.
    Assert ($browserProcesses[0].WaitForExit(30000)) 'Owned browser process did not exit'
    Assert (-not $browserProcesses[1].HasExited) 'Independent browser process exited'
    Assert ((Browser-State $profiles[1]).reachable) 'Independent browser was affected'
    Write-Output 'Windows desktop checks passed: syntax, exact argument transport, atomic control, shortcut icon, installer render and isolated browser shutdown.'
} finally {
    foreach ($profile in $profiles) { Close-OwnedBrowser $profile }
    foreach ($view in $views) { $view.Form.Close(); $view.Form.Dispose() }
}
