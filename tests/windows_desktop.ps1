$ErrorActionPreference = 'Stop'
$VerbosePreference = 'Continue'
$repo = Split-Path $PSScriptRoot -Parent
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
$temporary = Join-Path $env:TEMP ('bactologix-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $temporary | Out-Null
python (Join-Path $repo 'scripts/build_windows_bundle.py')
Expand-Archive -LiteralPath (Join-Path $repo 'dist/Bactologix-Windows.zip') -DestinationPath $temporary
. (Join-Path $temporary 'Bactologix/Bactologix.ps1') -TestMode
$views = @(); $profiles = @()
try {
    $scriptPath = Join-Path $repo 'scripts/windows/Bactologix.ps1'
    $tokens=$null; $errors=$null
    $null = [Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$tokens,[ref]$errors)
    Assert ($errors.Count -eq 0) ($errors | Out-String)
    $values = @('space and apostrophe''s','double"quote','C:\ends\','a&b;$x', '')
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
        $deadline = [Diagnostics.Stopwatch]::StartNew()
        while ($deadline.Elapsed.TotalSeconds -lt 45 -and -not (Browser-State $profile).reachable) { Start-Sleep -Milliseconds 200 }
        Assert ((Browser-State $profile).pages -ge 1) 'Dedicated browser was not observable'
    }
    Close-OwnedBrowser $profiles[0]
    Write-Output ('Immediately after close: ' + ((Browser-State $profiles[0]) | ConvertTo-Json -Compress))
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    while ($deadline.Elapsed.TotalSeconds -lt 15 -and (Browser-State $profiles[0]).reachable) { Start-Sleep -Milliseconds 200 }
    Write-Output ('After wait: ' + ((Browser-State $profiles[0]) | ConvertTo-Json -Compress))
    Assert (-not (Browser-State $profiles[0]).reachable) 'Owned browser did not close'
    Assert ((Browser-State $profiles[1]).reachable) 'Independent browser was affected'
    Write-Output 'Windows desktop checks passed: syntax, exact argument transport, atomic control, shortcut icon, installer render and isolated browser shutdown.'
} finally {
    foreach ($profile in $profiles) { Close-OwnedBrowser $profile }
    foreach ($view in $views) { $view.Form.Close(); $view.Form.Dispose() }
}
