$ErrorActionPreference = 'Stop'
$VerbosePreference = 'Continue'
$repo = Split-Path $PSScriptRoot -Parent
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
$temporary = Join-Path $env:TEMP ('bactologix-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $temporary | Out-Null
python (Join-Path $repo 'scripts/build_windows_bundle.py')
Expand-Archive -LiteralPath (Join-Path $repo 'dist/Bactologix-Windows.zip') -DestinationPath $temporary
. (Join-Path $temporary 'Bactologix/Bactologix.ps1') -TestMode
$originalSetupRoot = $script:Root
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
    # Some Windows builds return a nonzero code for valid --help. Compare with
    # a literal bare-option invocation rather than assuming Unix help semantics.
    $baselineInfo = [Diagnostics.ProcessStartInfo]::new('wsl.exe','--help')
    $baselineInfo.UseShellExecute = $false; $baselineInfo.CreateNoWindow = $true
    $baselineInfo.RedirectStandardOutput = $true; $baselineInfo.RedirectStandardError = $true
    $baseline = [Diagnostics.Process]::Start($baselineInfo)
    $baselineOut = $baseline.StandardOutput.ReadToEndAsync(); $baselineErr = $baseline.StandardError.ReadToEndAsync()
    Assert ($baseline.WaitForExit(30000)) 'Native WSL baseline timed out'
    Assert ($wslHelp.Process.ExitCode -eq $baseline.ExitCode -and $helpText -match '--distribution' -and $helpText -match '--exec') ('Native WSL option parsing failed: ' + $helpText + $wslHelp.Err.Result)
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
    # First-install paths are mocked; CI never enables features, installs a distro or reboots.
    $setupFunctions = @('Enable-WslFeatures','Get-WindowsBootIdentity','Read-Json','Invoke-SetupElevation','Get-WslExecutable','Get-RegisteredDistributions','Get-InstalledDistributions','Get-WslSetupState','Confirm-UbuntuInstall','Install-WslRuntime','Install-UbuntuForCurrentUser','Save-SetupContinuation','Show-SetupRestart','Initialize-UbuntuUser','Write-SetupLog','Show-Stage','Show-SetupOutcome')
    $savedSetupFunctions = @{}
    foreach ($name in $setupFunctions) { $savedSetupFunctions[$name] = (Get-Item "Function:$name").ScriptBlock }
    try {
        function Read-Json { param($Path) return $script:PendingSetup }
        function Get-WindowsBootIdentity { return 'boot-now' }
        $script:PendingSetup = $null
        function Get-WslExecutable { return $null }
        function Get-RegisteredDistributions { return @() }
        $state = Get-WslSetupState
        Assert ($state.NeedsRuntime -and $state.Names.Count -eq 0 -and -not $state.Executable) 'Virgin Windows PC did not enter runtime installation'
        function Get-WslExecutable { return 'wsl.exe' }
        function Get-InstalledDistributions { throw 'WSL is not installed yet' }
        $state = Get-WslSetupState
        Assert ($state.NeedsRuntime -and $state.Diagnostic -match 'not installed') 'First-list failure blocked fresh setup'
        function Get-RegisteredDistributions { return @('Ubuntu') }
        $message = ''
        try { Get-WslSetupState } catch { $message = $_.Exception.Message }
        Assert ($message -match 'not installed') 'Existing registration failure was misclassified as a fresh PC'
        function Get-RegisteredDistributions { throw 'Registry access denied' }
        $message = ''
        try { Get-WslSetupState } catch { $message = $_.Exception.Message }
        Assert ($message -match 'Registry access denied') 'Unreadable registration was treated as empty'
        Assert ((Get-InstallOutcome 0 'Fixture') -eq 'ready') 'Success exit code misclassified'
        Assert ((Get-InstallOutcome 3010 'Fixture') -eq 'restart') 'Restart exit code misclassified'
        $message = ''
        try { Get-InstallOutcome 5 'Fixture' } catch { $message = $_.Exception.Message }
        Assert ($message -match 'exit 5, 0x00000005') 'Installation failure lost its exit code'
        $message = ''
        try { Get-InstallOutcome -1 'Ubuntu installation' 'WSL_E_VIRTUAL_MACHINE_PLATFORM_REQUIRED' } catch { $message = $_.Exception.Message }
        Assert ($message -match '0xFFFFFFFF' -and $message -match 'WSL_E_VIRTUAL_MACHINE_PLATFORM_REQUIRED') 'Underlying WSL error was discarded'
        function Write-SetupLog { param($Message) }
        function Show-Stage { param($View,$Title,$Detail) }
        function Show-SetupOutcome { param($View,$State,$Title,$Detail,$Diagnostic) }
        # Execute the real elevated feature script in a separate PowerShell
        # process with mocked servicing commands; never modify the CI machine.
        function Invoke-SetupElevation {
            param($Executable,$Arguments)
            $command = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($Arguments[-1]))
            $fixture = '$fixtureState = ' + ("'" + $script:FeatureState + "'; ") + @'
function Get-WindowsOptionalFeature { param($Online,$FeatureName) return @{State=$fixtureState} }
function Enable-WindowsOptionalFeature { param($Online,$FeatureName,$All,$NoRestart) throw 'Unexpected real feature enable' }
'@
            # Switch-compatible mocked functions and a marker for disabled state.
            $fixture = $fixture.Replace('param($Online,$FeatureName)', 'param([switch]$Online,$FeatureName)').Replace('param($Online,$FeatureName,$All,$NoRestart)', 'param([switch]$Online,$FeatureName,[switch]$All,[switch]$NoRestart)')
            if ($script:FeatureState -eq 'Disabled') { $fixture = $fixture.Replace("throw 'Unexpected real feature enable'", 'return @{RestartNeeded=$false}') }
            $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($fixture + "`n" + $command))
            $process = Start-Process $Executable -ArgumentList @('-NoProfile','-EncodedCommand',$encoded) -Wait -PassThru -WindowStyle Hidden
            return $process.ExitCode
        }
        foreach ($featureState in @('Enabled','Disabled','EnablePending','DisablePending')) {
            $script:FeatureState = $featureState
            $expected = if ($featureState -eq 'Enabled') { 'ready' } else { 'restart' }
            Assert ((Enable-WslFeatures $null) -eq $expected) ('Feature servicing state mishandled: ' + $featureState)
        }
        function Enable-WslFeatures { param($View) $script:SetupCalls += 'features'; return $script:FeatureOutcome }
        $script:FeatureOutcome = 'ready'
        function Save-SetupContinuation { param($Name,$Phase) $script:SetupPhases += $Phase }
        function Show-SetupRestart { param($View,$Name) $script:SetupCalls += 'restart' }
        function Confirm-UbuntuInstall { param($Name) return $script:SetupConsent }
        function Install-WslRuntime { param($View,$Executable) $script:SetupCalls += 'runtime'; return $script:RuntimeOutcome }
        function Install-UbuntuForCurrentUser { param($View,$Name) $script:SetupCalls += 'ubuntu-current-user'; return $script:DistroOutcome }
        function Initialize-UbuntuUser { param($View,$Name) $script:SetupCalls += 'initialize' }
        function Get-InstalledDistributions { return @('Ubuntu') }
        function Get-WslSetupState { return $script:SetupState }
        $script:SetupState = @{Names=@();NeedsRuntime=$true;Executable='wsl.exe';Diagnostic='fresh fixture'}
        $script:SetupConsent=$true; $script:SetupCalls=@(); $script:SetupPhases=@(); $script:RuntimeOutcome='restart'; $script:DistroOutcome='ready'
        $answer = Prepare-Ubuntu $null 'Ubuntu'
        Assert (-not $answer -and ($script:SetupCalls -join ',') -eq 'features,runtime,restart') 'Runtime reboot continued into Ubuntu or app installation'
        $script:SetupCalls=@(); $script:RuntimeOutcome='ready'
        $answer = Prepare-Ubuntu $null 'Ubuntu'
        Assert ($answer -eq 'Ubuntu' -and ($script:SetupCalls -join ',') -eq 'features,runtime,ubuntu-current-user,initialize') 'Fresh setup did not install and initialize Ubuntu for the current user'
        $script:SetupCalls=@(); $script:SetupConsent=$false
        $answer = Prepare-Ubuntu $null 'Ubuntu'
        Assert (-not $answer -and $script:SetupCalls.Count -eq 0) 'Declining installation still installed components'
        $script:SetupCalls=@(); $script:SetupConsent=$true; $script:SetupState.NeedsRuntime=$false; $script:DistroOutcome='restart'
        $answer = Prepare-Ubuntu $null 'Ubuntu'
        Assert (-not $answer -and ($script:SetupCalls -join ',') -eq 'features,ubuntu-current-user,restart') 'Distro reboot was ignored'
        $script:SetupCalls=@(); $script:FeatureOutcome='restart'; $script:DistroOutcome='ready'
        $answer = Prepare-Ubuntu $null 'Ubuntu'
        Assert (-not $answer -and ($script:SetupCalls -join ',') -eq 'features,restart') 'Successful WSL listing bypassed disabled Windows features'
        $script:SetupCalls=@(); $script:PendingSetup=@{phase='restart-required';boot='boot-now'}
        $answer = Prepare-Ubuntu $null 'Ubuntu'
        Assert (-not $answer -and ($script:SetupCalls -join ',') -eq 'restart') 'Continuation before reboot ran installation'
        $script:SetupCalls=@(); $script:PendingSetup=@{phase='restart-required';boot='previous-boot'}; $script:FeatureOutcome='ready'
        $answer = Prepare-Ubuntu $null 'Ubuntu'
        Assert ($answer -eq 'Ubuntu' -and ($script:SetupCalls -join ',') -eq 'features,ubuntu-current-user,initialize') 'Continuation after reboot did not resume'
        $script:PendingSetup=$null
        $script:SetupCalls=@(); $script:SetupState.Names=@('Ubuntu-24.04')
        $answer = Prepare-Ubuntu $null 'Ubuntu'
        Assert ($answer -eq 'Ubuntu-24.04' -and $script:SetupCalls.Count -eq 0) 'Existing Ubuntu was unnecessarily reinstalled'
        Write-Output 'Fresh-PC setup state tests passed: missing WSL, first-list failure, existing-registration errors, consent, reboot, current-user installation and Ubuntu reuse.'
    } finally {
        foreach ($name in $setupFunctions) { Set-Item "Function:$name" $savedSetupFunctions[$name] }
    }

    # Account readiness regressions: exercise the real orchestration without
    # opening a terminal or modifying the CI runner's WSL installation.
    $accountFunctions = @('Capture','Open-UbuntuAccountWindow','Show-Stage','Write-SetupLog')
    $savedAccountFunctions = @{}
    foreach ($name in $accountFunctions) { $savedAccountFunctions[$name] = (Get-Item "Function:$name").ScriptBlock }
    try {
        function Show-Stage { param($View,$Title,$Detail) }
        function Write-SetupLog { param($Message) }
        function Capture {
            param($Arguments)
            Assert (($Arguments -join ',') -eq '--distribution,Ubuntu,--exec,id,-u') 'Account probe changed distribution or command'
            $value = $script:AccountResponses.Dequeue()
            if ($value -eq 'probe-failed') { throw 'fixture WSL startup failed' }
            return $value
        }
        function Open-UbuntuAccountWindow { param($View,$Name) $script:AccountWindows++; return $script:AccountExit }
        foreach ($case in @(
            @{Responses=@('1000');Exit=0;Windows=0;Ready=$true},
            @{Responses=@('0','1000');Exit=0;Windows=1;Ready=$true},
            @{Responses=@('0','1000');Exit=-1073741510;Windows=1;Ready=$true},
            @{Responses=@('probe-failed','1000');Exit=-1;Windows=1;Ready=$true},
            @{Responses=@('0','0');Exit=0;Windows=1;Ready=$false},
            @{Responses=@('0','0');Exit=-1073741510;Windows=1;Ready=$false},
            @{Responses=@('0','probe-failed');Exit=-1;Windows=1;Ready=$false},
            @{Responses=@('invalid','invalid');Exit=0;Windows=1;Ready=$false}
        )) {
            $script:AccountResponses = [Collections.Queue]::new()
            foreach ($value in $case.Responses) { $script:AccountResponses.Enqueue($value) }
            $script:AccountWindows=0; $script:AccountExit=$case.Exit; $failure=''
            try { Initialize-UbuntuUser $null 'Ubuntu' } catch { $failure=$_.Exception.Message }
            Assert (($failure -eq '') -eq $case.Ready) ('Account readiness result incorrect: ' + $failure)
            Assert ($script:AccountWindows -eq $case.Windows) 'Ready account opened an unnecessary terminal'
            Assert ($script:AccountResponses.Count -eq 0) 'Account readiness was not rechecked'
            if (-not $case.Ready) { Assert ($failure -match 'setup is incomplete.*Continue Bactologix Setup') 'Missing actionable continuation message' }
        }
        Write-Output 'Ubuntu account tests passed: ready-user skip, normal exit, window close, root-only, invalid UID and WSL failure.'
    } finally {
        foreach ($name in $accountFunctions) { Set-Item "Function:$name" $savedAccountFunctions[$name] }
    }

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
    Add-Type -TypeDefinition @'
using System.IO;
using System.Threading;
using System.Threading.Tasks;
public static class ControlFileFixture {
 public static Task Hold(string path, int milliseconds, bool shareDelete) {
  var sharing = shareDelete ? FileShare.ReadWrite | FileShare.Delete : FileShare.Read;
  var reader = new FileStream(path, FileMode.Open, FileAccess.Read, sharing);
  return Task.Run(() => { try { Thread.Sleep(milliseconds); } finally { reader.Dispose(); } });
 }
}
'@
    $controlPath = Join-Path $temporary 'state.json'
    $readerLock = [ControlFileFixture]::Hold($controlPath,250,$false)
    Write-Json $controlPath @{session='test';phase='after-transient-lock'}
    $readerLock.Wait()
    Assert ((Read-Json $controlPath).phase -eq 'after-transient-lock') 'Transient reader lock stopped control updates'
    $readerLock = [ControlFileFixture]::Hold($controlPath,250,$true)
    Write-Json $controlPath @{session='test';phase='shared-reader'}
    $readerLock.Wait()
    Assert ((Read-Json $controlPath).phase -eq 'shared-reader') 'Delete-sharing reader prevented atomic replacement'
    $readerLock = [ControlFileFixture]::Hold($controlPath,3500,$false)
    $failure = ''
    try { Write-Json $controlPath @{session='test';phase='must-not-replace'} } catch { $failure = $_.Exception.Message }
    finally { $readerLock.Wait() }
    Assert ($failure -like '*state.json*Windows error*') 'Persistent lock failure lacked actionable diagnostics'
    Assert ((Read-Json $controlPath).phase -eq 'shared-reader') 'Failed replacement damaged last valid control file'
    Assert (@(Get-ChildItem $temporary -Filter '*.writing').Count -eq 0) 'Control temporary files leaked'
    $presence = @{ready=$false;missing_since=$null}
    $present = @{reachable=$true;pages=1}; $absent = @{reachable=$false;pages=0}
    Assert (-not (Update-BrowserPresence $presence $absent 0)) 'Startup was treated as a close'
    Assert (-not (Update-BrowserPresence $presence $present 1)) 'Live browser was treated as a close'
    Assert (-not (Update-BrowserPresence $presence $absent 2)) 'One missed response triggered shutdown'
    Assert (-not (Update-BrowserPresence $presence $absent 11.9)) 'Transient absence triggered shutdown'
    Assert (-not (Update-BrowserPresence $presence $present 12)) 'Restored browser triggered shutdown'
    Assert (-not (Update-BrowserPresence $presence $absent 13)) 'Recovery did not reset absence timer'
    Assert (Update-BrowserPresence $presence $absent 23) 'Sustained close was not detected'
    Assert (-not (Update-BrowserPresence $presence $present 24)) 'Live browser did not recover'
    Assert (-not (Update-BrowserPresence $presence @{reachable=$true;pages=0} 25)) 'One empty tab response triggered shutdown'
    Write-Output 'Control-file sharing, bounded retry, preservation and browser grace checks passed.'

    # Cancellation before an acknowledgement is not delivery. Exercise retries
    # without opening unrelated browsers or allowing endpoint retargeting.
    $savedEndpoint = (Get-Item Function:Browser-Endpoint).ScriptBlock
    $savedCloseRequest = (Get-Item Function:Send-BrowserCloseRequest).ScriptBlock
    try {
        $script:CloseEndpointReads = 0; $script:CloseAttempts = 0; $script:CloseTargets = @()
        function Browser-Endpoint {
            param($Profile)
            Assert ($Profile -eq 'fixture-owned') 'Close retry changed the requested profile'
            $script:CloseEndpointReads++
            # A second lookup would represent a replaced profile endpoint.
            return @{port=1234;socket=('/devtools/browser/' + $script:CloseEndpointReads)}
        }
        function Send-BrowserCloseRequest {
            param($Endpoint)
            $script:CloseAttempts++
            $script:CloseTargets += ($Endpoint.port.ToString() + $Endpoint.socket)
            if ($script:CloseAttempts -eq 1) { throw [OperationCanceledException]::new('Fixture handshake timeout') }
        }
        Close-OwnedBrowser 'fixture-owned'
        Assert ($script:CloseAttempts -eq 2) 'Cancelled close request was not retried once before success'
        Assert ($script:CloseEndpointReads -eq 1 -and (@($script:CloseTargets | Select-Object -Unique)).Count -eq 1) 'Retry retargeted another browser endpoint'
        $script:CloseEndpointReads = 0; $script:CloseAttempts = 0
        function Send-BrowserCloseRequest {
            param($Endpoint)
            $script:CloseAttempts++
            throw [OperationCanceledException]::new('Fixture persistent timeout')
        }
        Close-OwnedBrowser 'fixture-owned'
        Assert ($script:CloseAttempts -eq 3 -and $script:CloseEndpointReads -eq 1) 'Close retries were unbounded or reread the endpoint'
        $script:CloseAttempts = 0
        function Browser-Endpoint { param($Profile) return $null }
        Close-OwnedBrowser 'fixture-owned'
        Assert ($script:CloseAttempts -eq 0) 'Missing profile endpoint still sent a close request'
        Write-Output 'Owned-browser close retries passed: timeout recovery, bounded failure and fixed endpoint.'
    } finally {
        Set-Item Function:Browser-Endpoint $savedEndpoint
        Set-Item Function:Send-BrowserCloseRequest $savedCloseRequest
    }

    $shortcut = Join-Path $temporary 'Bactologix.lnk'
    New-Shortcut $temporary $shortcut
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    Assert ($link.IconLocation -eq ((Join-Path $temporary 'brand.ico') + ',0')) 'Shortcut icon not bound to app brand'
    Assert ($link.TargetPath -like '*wscript.exe') 'Silent shortcut entry missing'
    function Save-SetupPreview($View, [string]$Name) {
        Update-SetupLayout $View
        $View.Form.PerformLayout(); [Windows.Forms.Application]::DoEvents()
        $bitmap = New-Object Drawing.Bitmap($View.Form.Width, $View.Form.Height)
        try {
            $View.Form.DrawToBitmap($bitmap, [Drawing.Rectangle]::new(0,0,$View.Form.Width,$View.Form.Height))
            $bitmap.Save((Join-Path $repo ("windows-setup-" + $Name + ".png")))
        } finally { $bitmap.Dispose() }
    }
    $script:Root = Join-Path $temporary 'welcome-no-install'
    $view = New-Progress 'Bactologix setup preview'
    $views += $view
    Assert ($null -ne $view.Picture.Image -and $null -ne $view.Form.Icon) 'Packaged setup branding was not loaded'
    Show-SetupWelcome $view
    Assert ($view.State -eq 'welcome' -and $view.AllowClose) 'Welcome did not allow a no-work exit'
    Assert ($view.Primary.Visible -and -not $view.Bar.Visible) 'Welcome implies installation already started'
    Assert ($view.Steps.Text -match 'Internet.*administrator.*restart') 'Welcome omitted setup requirements'
    Assert (-not (Test-Path -LiteralPath $script:Root)) 'Welcome wrote installation state before Start'
    Save-SetupPreview $view 'welcome'
    $view.Primary.PerformClick()
    Assert ($view.Action -eq 'start') 'Start setup action is not connected'
    New-Item -ItemType Directory $script:Root | Out-Null
    Set-SetupPhase $view 'working'; Set-SetupSteps $view 3
    Show-Stage $view 'Preparing your application environment' 'Reusing an existing environment when available. This step may take several minutes while packages are checked or downloaded.'
    Assert (-not $view.AllowClose -and -not $view.Primary.Visible -and -not $view.Secondary.Visible) 'Working state offers an unsafe exit'
    Assert ($view.Bar.Style -eq 'Marquee' -and $view.Bar.Value -eq 0) 'Working state invented percentage progress'
    Assert ($view.StepNumbers[0].Text -eq [string][char]0x2713 -and $view.StepNumbers[2].Text -eq '3') 'Current named step is unclear'
    $view.Form.Close()
    Assert (-not $view.Form.IsDisposed -and $view.Form.Visible) 'Closing progress abandoned active work'
    Save-SetupPreview $view 'preview'
    Show-SetupOutcome $view 'restart' 'Restart Windows to continue' 'Save your work and restart Windows. Your progress is saved. Then open Continue Bactologix Setup on your desktop.'
    Assert ($view.AllowClose -and -not $view.Primary.Visible -and -not $view.Bar.Visible) 'Restart state still appears to install'
    Assert ($view.Steps.Text -match 'Continue Bactologix Setup') 'Restart state lacks the continuation path'
    Save-SetupPreview $view 'restart'
    Show-SetupOutcome $view 'error' 'Setup needs your attention' 'Setup stopped before it could finish. Review the details and logs before trying again.' 'Example: Ubuntu could not start. The underlying Windows diagnostic is preserved here. No saved results were removed.'
    Assert ($view.Diagnostic.Visible -and $view.Diagnostic.ReadOnly -and $view.PrimaryAction -eq 'retry') 'Failure details or retry action are missing'
    $view.Primary.PerformClick()
    Assert ($view.Action -eq 'retry') 'Retry action is not connected'
    Save-SetupPreview $view 'error'
    Show-SetupOutcome $view 'complete' 'Your workspace is ready.' 'Bactologix is installed and the desktop shortcut is ready. Open the app now or return later.'
    Assert ($view.Bar.Value -eq 100 -and $view.PrimaryAction -eq 'open') 'Completion did not offer explicit opening'
    Assert (@($view.StepNumbers | Where-Object { $_.Text -ne [string][char]0x2713 }).Count -eq 0) 'Completed steps were not recorded'
    Save-SetupPreview $view 'complete'
    # Dev packaging substitutes the visible product name; its longer label must
    # wrap within the sidebar instead of widening or clipping the content.
    $view.Brand.Text = 'Bactologix-Dev'
    Show-SetupWelcome $view
    Save-SetupPreview $view 'dev-welcome'
    Assert ($view.Brand.Width -le $view.Brand.MaximumSize.Width) 'Dev branding exceeded its sidebar width'
    $view.Brand.Text = 'Bactologix'
    # Exercise enlarged controls/text in a constrained display; content may scroll,
    # but the primary and close actions must stay inside the visible form.
    $large = New-Progress 'Bactologix enlarged-layout preview'; $views += $large
    $large.Form.Scale([Drawing.SizeF]::new(1.5,1.5))
    Show-SetupOutcome $large 'error' 'Setup needs your attention' 'A long diagnostic must remain readable when the display scale is enlarged. The setup actions remain available below the scrollable content.' ('Example diagnostic. ' * 40)
    Fit-SetupWindow $large; Update-SetupLayout $large; [Windows.Forms.Application]::DoEvents()
    Save-SetupPreview $large 'enlarged'
    foreach ($diagnostic in $large.LayoutDiagnostics) { Write-Output ('Enlarged footer measurement: ' + $diagnostic) }
    foreach ($control in @($large.Content,$large.Scroll,$large.Actions)) {
        $position = $large.Form.PointToClient($control.PointToScreen([Drawing.Point]::Empty))
        $bounds = "type=$($control.GetType().Name) x=$($position.X) y=$($position.Y) size=$($control.Size); client=$($large.Form.ClientSize)"
        Assert ($position.X -ge 0 -and $position.Y -ge 0 -and $position.X+$control.Width -le $large.Form.ClientSize.Width -and $position.Y+$control.Height -le $large.Form.ClientSize.Height) ('Enlarged setup container exceeded the viewport: ' + $bounds)
    }
    Assert ($large.Scroll.DisplayRectangle.Width -le $large.Scroll.ClientSize.Width) 'Enlarged setup content overflowed horizontally'
    foreach ($button in @($large.Primary,$large.Secondary,$large.Logs)) {
        $position = $large.Form.PointToClient($button.PointToScreen([Drawing.Point]::Empty))
        $bounds = "button='$($button.Text)' x=$($position.X) y=$($position.Y) width=$($button.Width) height=$($button.Height); client=$($large.Form.ClientSize); footer=$($large.Actions.Bounds)"
        Write-Output ('Enlarged layout: ' + $bounds)
        Assert ($position.X -ge 0 -and $position.Y -ge 0 -and $position.X+$button.Width -le $large.Form.ClientSize.Width -and $position.Y+$button.Height -le $large.Form.ClientSize.Height) ('Enlarged setup actions were clipped: ' + $bounds)
    }
    $large.Form.Hide(); $view.Form.Hide()
    # Exercise the setup coordinator independently from WSL or app installation.
    $uiFunctions = @('Show-SetupWelcome','Wait-SetupAction','Install-Desktop','Run-Desktop','Show-SetupOutcome')
    $savedUiFunctions = @{}
    foreach ($name in $uiFunctions) { $savedUiFunctions[$name] = (Get-Item "Function:$name").ScriptBlock }
    try {
        function Show-SetupWelcome { param($View) $script:WelcomeCount++ }
        function Wait-SetupAction { param($View) return $script:UiActions.Dequeue() }
        function Install-Desktop {
            param($View)
            $script:InstallCount++
            $outcome = $script:UiOutcomes.Dequeue()
            if ($outcome -eq 'throw') { throw 'preserved fixture diagnostic' }
            $View.Outcome = $outcome
        }
        function Run-Desktop { param($View) $script:LaunchCount++ }
        function Show-SetupOutcome { param($View,$State,$Title,$Detail,$Diagnostic) $View.Outcome=$State; $script:UiDiagnostic=$Diagnostic }
        foreach ($case in @(
            @{Actions=@('close');Outcomes=@();Code=0;Installs=0;Launches=0},
            @{Actions=@('start','close');Outcomes=@('complete');Code=0;Installs=1;Launches=0},
            @{Actions=@('start','open');Outcomes=@('complete');Code=0;Installs=1;Launches=1},
            @{Actions=@('start','close');Outcomes=@('restart');Code=3010;Installs=1;Launches=0},
            @{Actions=@('start','close');Outcomes=@('paused');Code=1223;Installs=1;Launches=0},
            @{Actions=@('start','close');Outcomes=@('throw');Code=1;Installs=1;Launches=0},
            @{Actions=@('start','retry','close');Outcomes=@('throw','complete');Code=0;Installs=2;Launches=0}
        )) {
            $script:UiActions=[Collections.Queue]::new(); foreach ($item in $case.Actions) { $script:UiActions.Enqueue($item) }
            $script:UiOutcomes=[Collections.Queue]::new(); foreach ($item in $case.Outcomes) { $script:UiOutcomes.Enqueue($item) }
            $script:WelcomeCount=0; $script:InstallCount=0; $script:LaunchCount=0; $script:UiDiagnostic=''
            $code = Invoke-SetupInterface $view
            Assert ($code -eq $case.Code -and $script:InstallCount -eq $case.Installs -and $script:LaunchCount -eq $case.Launches -and $script:WelcomeCount -eq 1) 'Setup coordinator started unrequested work or lost its outcome'
            Assert ($script:UiActions.Count -eq 0 -and $script:UiOutcomes.Count -eq 0) 'Setup coordinator ignored an expected action'
            if ('throw' -in $case.Outcomes) { Assert ($script:UiDiagnostic -eq 'preserved fixture diagnostic') 'Setup failure discarded the original diagnostic' }
        }
        Write-Output 'Installer UX passed: explicit Start, no-work Close, guarded progress, restart continuation, preserved errors, explicit Open, bounded layout and outcome exit codes.'
    } finally {
        foreach ($name in $uiFunctions) { Set-Item "Function:$name" $savedUiFunctions[$name] }
    }
    $browser = Find-Browser
    Write-Output ('Browser fixture: ' + $browser + ' version ' + (Get-Item $browser).VersionInfo.FileVersion)
    foreach ($name in @('owned','independent')) {
        $profile = Join-Path $temporary $name; $profiles += $profile
        $task = Start-Owned $browser @('--headless=new',"--user-data-dir=$profile",'--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--no-first-run','--disable-background-mode','about:blank')
        $browserProcesses += $task.Process
        $deadline = [Diagnostics.Stopwatch]::StartNew()
        while ($deadline.Elapsed.TotalSeconds -lt 45 -and -not (Browser-State $profile).reachable) { Start-Sleep -Milliseconds 200 }
        Assert ((Browser-State $profile).pages -ge 1) 'Dedicated browser was not observable'
        Write-Output ('Observed ' + $name + ' browser PID ' + $task.Process.Id + ' at ' + (Browser-Endpoint $profile).port)
    }
    Close-OwnedBrowser $profiles[0]
    # Headless Edge runs in this foreground process. Check actual termination:
    # its HTTP endpoint can briefly accept empty responses during teardown.
    $ownedExited = $browserProcesses[0].WaitForExit(30000)
    if (-not $ownedExited) {
        Write-Output ('Owned browser exit timeout; PID ' + $browserProcesses[0].Id + '; state ' + ((Browser-State $profiles[0]) | ConvertTo-Json -Compress))
        Write-Output ('Independent browser PID ' + $browserProcesses[1].Id + '; state ' + ((Browser-State $profiles[1]) | ConvertTo-Json -Compress))
    }
    Assert $ownedExited 'Owned browser process did not exit'
    Assert (-not $browserProcesses[1].HasExited) 'Independent browser process exited'
    Assert ((Browser-State $profiles[1]).reachable) 'Independent browser was affected'
    Write-Output 'Windows desktop checks passed: syntax, exact argument transport, atomic control, shortcut icon, installer render and isolated browser shutdown.'
} finally {
    $script:Root = $originalSetupRoot
    foreach ($profile in $profiles) { Close-OwnedBrowser $profile }
    foreach ($view in $views) { $view.AllowClose=$true; $view.Form.Close(); $view.Form.Dispose() }
}



