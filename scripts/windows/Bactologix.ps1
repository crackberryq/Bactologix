param([switch]$Setup, [string]$Distro = 'Ubuntu', [switch]$TestMode)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
[Windows.Forms.Application]::SetCompatibleTextRenderingDefault($false)
$script:Root = Join-Path $env:LOCALAPPDATA 'Bactologix'
$script:Utf8 = New-Object System.Text.UTF8Encoding($false)

function Quote-Argument([string]$Value) {
    # WSL's option parser does not treat quoted switches like CRT argv.
    # Keep simple options/names bare; quote values that need escaping.
    if ($Value -match '\A[A-Za-z0-9_./:\\=-]+\z') { return $Value }
    $result = '"'; $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') { $result += ('\' * (2 * $slashes + 1)) + '"' }
        else { $result += ('\' * $slashes) + $character }
        $slashes = 0
    }
    return $result + ('\' * (2 * $slashes)) + '"'
}
function Start-Owned([string]$Executable, [string[]]$Arguments, [switch]$Unicode, [switch]$Visible) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = (($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = -not $Visible
    $info.RedirectStandardOutput = -not $Visible
    $info.RedirectStandardError = -not $Visible
    if (-not $Visible) {
        $info.StandardOutputEncoding = $(if ($Unicode) { [Text.Encoding]::Unicode } else { [Text.Encoding]::UTF8 })
        $info.StandardErrorEncoding = [Text.Encoding]::UTF8
    }
    $process = [Diagnostics.Process]::Start($info)
    $bytes = $null; $copy = $null; $output = $null
    if ($Unicode -and -not $Visible) {
        $bytes = New-Object IO.MemoryStream
        $copy = $process.StandardOutput.BaseStream.CopyToAsync($bytes)
    } elseif (-not $Visible) { $output = $process.StandardOutput.ReadToEndAsync() }
    return [pscustomobject]@{ Process = $process; Out = $output; Bytes = $bytes; Copy = $copy; Err = $(if (-not $Visible) { $process.StandardError.ReadToEndAsync() }) }
}
function Decode-WslOutput([byte[]]$Bytes) {
    # WSL versions/settings emit either UTF-8 or UTF-16, with or without BOM.
    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 254 -and $Bytes[1] -eq 255) { $encoding = [Text.Encoding]::BigEndianUnicode }
    elseif (($Bytes.Length -ge 2 -and $Bytes[0] -eq 255 -and $Bytes[1] -eq 254) -or $Bytes -contains 0) { $encoding = [Text.Encoding]::Unicode }
    else { $encoding = [Text.Encoding]::UTF8 }
    return $encoding.GetString($Bytes).Trim([char]0xFEFF).Trim()
}
function Get-WslExecutable {
    foreach ($directory in @('Sysnative','System32')) {
        $path = Join-Path $env:WINDIR "$directory\wsl.exe"
        if (Test-Path -LiteralPath $path) { return $path }
    }
    return $null
}
function Get-RegisteredDistributions {
    # Registration is per Windows user. Never inspect another administrator's hive.
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $key -ErrorAction Stop)) { return @() }
    return @(Get-ChildItem -LiteralPath $key -ErrorAction Stop | ForEach-Object {
        $item = Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction Stop
        if ($item.DistributionName) { [string]$item.DistributionName }
    })
}
function Get-WslSetupState {
    $registered = @(Get-RegisteredDistributions)
    $executable = Get-WslExecutable
    if (-not $executable) {
        if ($registered.Count) { throw 'Existing Linux distributions are registered, but wsl.exe is missing. Repair Windows WSL first; Setup will not replace existing distributions.' }
        return @{Names=@(); NeedsRuntime=$true; Executable=$null; Diagnostic='Windows WSL command is not installed.'}
    }
    try {
        $names = @(Get-InstalledDistributions)
        return @{Names=$names; NeedsRuntime=$false; Executable=$executable; Diagnostic='WSL distribution listing succeeded.'}
    } catch {
        if ($registered.Count) { throw }
        # A fresh PC can return nonzero for --list before WSL or a distro exists.
        # Preserve the diagnostic, then offer first-time setup instead of aborting.
        return @{Names=@(); NeedsRuntime=$true; Executable=$executable; Diagnostic=$_.Exception.Message}
    }
}
function Write-SetupLog([string]$Message) {
    New-Item -ItemType Directory -Force -Path $script:Root | Out-Null
    [IO.File]::AppendAllText((Join-Path $script:Root 'setup-windows.log'), ([DateTime]::UtcNow.ToString('o') + ' ' + $Message + "`r`n"), $script:Utf8)
}
function Get-InstallOutcome([int]$Code, [string]$Stage, [string]$Diagnostic = '') {
    if ($Code -eq 0) { return 'ready' }
    if ($Code -in @(3010,1641)) { return 'restart' }
    $hex = [BitConverter]::ToUInt32([BitConverter]::GetBytes($Code),0).ToString('X8')
    throw "$Stage failed (exit $Code, 0x$hex). Details: $Diagnostic See $script:Root\setup-windows.log. Existing Linux distributions were not removed."
}
function Invoke-SetupElevation([string]$Executable, [string[]]$Arguments) {
    try {
        $task = Start-Process -FilePath $Executable -ArgumentList (($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' ') -Verb RunAs -PassThru
        while (-not $task.WaitForExit(200)) { [Windows.Forms.Application]::DoEvents() }
        $task.Refresh()
        Write-SetupLog ('Windows component installation exited: ' + $task.ExitCode)
        return $task.ExitCode
    } catch {
        Write-SetupLog ('Windows component installation could not start: ' + $_.Exception.Message)
        throw 'Windows component setup did not finish. If administrator permission was cancelled, rerun Setup and approve the Windows prompt. See setup-windows.log for details.'
    }
}
function Get-WindowsBootIdentity {
    return (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime.ToUniversalTime().ToString('o')
}
function Enable-WslFeatures($View) {
    Show-Stage $View 'Checking required Windows features' 'Windows may request administrator permission to check and enable WSL and Virtual Machine Platform. Setup pauses for a restart if either feature changes.'
    # Fixed code only: inspect actual feature states, not the presence of wsl.exe
    # or a successful distribution listing. No user paths or names are elevated.
    $command = @'
$ErrorActionPreference = 'Stop'
try {
    $restart = $false
    foreach ($name in @('Microsoft-Windows-Subsystem-Linux','VirtualMachinePlatform')) {
        $feature = Get-WindowsOptionalFeature -Online -FeatureName $name
        if ($feature.State -eq 'EnablePending' -or $feature.State -eq 'DisablePending') {
            $restart = $true
        } elseif ($feature.State -ne 'Enabled') {
            $null = Enable-WindowsOptionalFeature -Online -FeatureName $name -All -NoRestart
            # Cross a reboot even when servicing reports success without 3010.
            $restart = $true
        }
    }
    if ($restart) { exit 3010 }
    exit 0
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $code = Invoke-SetupElevation (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') @('-NoProfile','-EncodedCommand',$encoded)
    return Get-InstallOutcome $code 'Required Windows WSL features'
}
function Test-UbuntuReady([string]$Name) {
    # Registration alone does not prove that the VM can start.
    $null = Capture @('--distribution',$Name,'--exec','/bin/true')
}
function Install-WslRuntime($View, [string]$Executable) {
    Show-Stage $View 'Installing Windows WSL support' 'Windows may ask for administrator permission. Ubuntu itself will be installed under your own Windows account.'
    if (-not $Executable) {
        # Fixed Windows command only; no downloaded or user-controlled elevated script.
        $command = '$ErrorActionPreference = "Stop"; try { $result = Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux,VirtualMachinePlatform -All -NoRestart; if ($result.RestartNeeded) { exit 3010 }; exit 0 } catch { Write-Error $_; exit 1 }'
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $code = Invoke-SetupElevation (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') @('-NoProfile','-EncodedCommand',$encoded)
        $null = Get-InstallOutcome $code 'Windows WSL features'
        # Even success must cross a reboot before relying on freshly enabled features.
        return 'restart'
    }
    $code = Invoke-SetupElevation $Executable @('--install','--no-distribution')
    return Get-InstallOutcome $code 'Windows WSL runtime'
}
function Install-UbuntuForCurrentUser($View, [string]$Name) {
    Show-Stage $View 'Installing Ubuntu' "Downloading $Name for your Windows account. This can take several minutes."
    $executable = Get-WslExecutable
    if (-not $executable) { throw 'WSL is not available yet. Restart Windows and use Continue Bactologix Setup.' }
    # Do not elevate distribution registration: a different admin account has a different WSL registry.
    $task = Start-Owned $executable @('--install','--distribution',$Name,'--no-launch') -Unicode
    while (-not $task.Process.WaitForExit(200)) { [Windows.Forms.Application]::DoEvents() }
    $null = $task.Copy.GetAwaiter().GetResult()
    try { $output = Decode-WslOutput ($task.Bytes.ToArray()) } finally { $task.Bytes.Dispose() }
    Write-SetupLog ("Ubuntu installation exit $($task.Process.ExitCode): " + $output + $task.Err.Result)
    return Get-InstallOutcome $task.Process.ExitCode 'Ubuntu installation' ($output + ' ' + $task.Err.Result)
}
function Save-SetupFiles {
    New-Item -ItemType Directory -Force -Path $script:Root | Out-Null
    foreach ($name in @('Bactologix.ps1','Bactologix.vbs','Bactologix.cmd','Bactologix-Setup.cmd','install_bactologix.py','brand.ico','brand.png')) {
        $source = Join-Path $PSScriptRoot $name; $target = Join-Path $script:Root $name
        if ([IO.Path]::GetFullPath($source) -ne [IO.Path]::GetFullPath($target)) { Copy-Item -LiteralPath $source -Destination $target -Force }
    }
}
function Save-SetupContinuation([string]$Name, [string]$Phase) {
    Write-Json (Join-Path $script:Root 'setup-state.json') @{ distro=$Name; phase=$Phase; boot=$(if ($Phase -eq 'restart-required') { Get-WindowsBootIdentity } else { $null }); updated=[DateTime]::UtcNow.ToString('o') }
    $path = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Continue Bactologix Setup.lnk'
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
    $shortcut.TargetPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $shortcut.Arguments = (@('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $script:Root 'Bactologix.ps1'),'-Setup','-Distro',$Name) | ForEach-Object { Quote-Argument $_ }) -join ' '
    $shortcut.WorkingDirectory = $script:Root
    $shortcut.IconLocation = (Join-Path $script:Root 'brand.ico') + ',0'
    $shortcut.Save()
}
function Show-SetupRestart($View, [string]$Name) {
    Save-SetupContinuation $Name 'restart-required'
    Show-SetupOutcome $View 'restart' 'Restart Windows to continue' 'Your setup progress is saved. Save your other work, restart Windows, then open Continue Bactologix Setup on your desktop. Bactologix will not restart this computer automatically.'
}
function Confirm-UbuntuInstall([string]$Name) {
    return ([Windows.Forms.MessageBox]::Show("Set up Windows WSL and install $Name now? Windows may request administrator permission and a restart. Existing Linux distributions will be kept.", 'Bactologix setup', 'YesNo','Question') -eq 'Yes')
}
function Get-UbuntuUserState([string]$Name) {
    try {
        # A successful command as a non-root default user proves both startup
        # and account readiness; neither a window exit code nor registration does.
        $uid = (Capture @('--distribution',$Name,'--exec','id','-u')).Trim()
        if ($uid -match '^0*[1-9][0-9]*$') { return @{Ready=$true; Diagnostic='Ubuntu regular user is ready.'} }
        if ($uid -match '^0+$') { return @{Ready=$false; Diagnostic='Ubuntu is using root. Finish creating and selecting your regular Linux user.'} }
        return @{Ready=$false; Diagnostic='Ubuntu returned an invalid user identity. Setup has not continued.'}
    } catch { return @{Ready=$false; Diagnostic=$_.Exception.Message} }
}
function Open-UbuntuAccountWindow($View, [string]$Name) {
    Show-Stage $View 'Finish your Ubuntu account setup' 'Complete the username and password prompts in Ubuntu. If a Linux prompt appears, type exit and press Enter. Setup then checks your account and continues.'
    [Windows.Forms.MessageBox]::Show('Complete the Ubuntu username and password prompts. If you reach a prompt ending in $, type exit and press Enter to return to Setup. Your password is not collected by Bactologix.', 'Ubuntu account setup', 'OK','Information') | Out-Null
    $task = Start-Process -FilePath (Get-WslExecutable) -ArgumentList ((@('--distribution',$Name) | ForEach-Object { Quote-Argument $_ }) -join ' ') -PassThru
    while (-not $task.WaitForExit(200)) { [Windows.Forms.Application]::DoEvents() }
    $task.Refresh()
    return $task.ExitCode
}
function Initialize-UbuntuUser($View, [string]$Name) {
    Show-Stage $View 'Checking your Ubuntu account' 'Checking whether Ubuntu already has a working regular user.'
    $state = Get-UbuntuUserState $Name
    if ($state.Ready) {
        Write-SetupLog 'Ubuntu regular user already ready; skipped account window.'
        return
    }
    $code = Open-UbuntuAccountWindow $View $Name
    # Closing the terminal can return STATUS_CONTROL_C_EXIT even after account
    # creation succeeded. Recheck actual readiness regardless of that exit code.
    $state = Get-UbuntuUserState $Name
    Write-SetupLog ("Ubuntu account window exit ${code}: " + $state.Diagnostic)
    if ($state.Ready) { return }
    throw "Ubuntu account setup is incomplete (window exit $code). $($state.Diagnostic) Use Continue Bactologix Setup to retry."
}
function Prepare-Ubuntu($View, [string]$Name) {
    $pending = Read-Json (Join-Path $script:Root 'setup-state.json')
    if ($pending -and $pending.phase -eq 'restart-required' -and $pending.boot -and $pending.boot -eq (Get-WindowsBootIdentity)) {
        Show-SetupRestart $View $Name
        return $null
    }
    $state = Get-WslSetupState
    Write-SetupLog $state.Diagnostic
    $names = @($state.Names)
    if ($Name -eq 'Ubuntu' -and $Name -notin $names) {
        $existing = @($names | Where-Object { $_ -like 'Ubuntu*' })
        if ($existing.Count) { $Name = $existing[0] }
    }
    if ($Name -in $names) {
        $pending = Read-Json (Join-Path $script:Root 'setup-state.json')
        if ($pending -and $pending.distro -eq $Name -and $pending.phase -eq 'ubuntu-user-initialization') { Initialize-UbuntuUser $View $Name }
        return $Name
    }
    if (-not [Environment]::Is64BitOperatingSystem -or [Environment]::OSVersion.Version.Build -lt 19041) {
        throw 'First-time setup requires 64-bit Windows 10 build 19041 or newer, or Windows 11. Update Windows, then rerun Setup.'
    }
    if (-not (Confirm-UbuntuInstall $Name)) {
        Show-SetupOutcome $View 'paused' 'Setup paused' 'Windows and Ubuntu installation was not started. Open Bactologix Setup again when you are ready.'
        return $null
    }
    Save-SetupContinuation $Name 'windows-prerequisites'
    $features = Enable-WslFeatures $View
    if ($features -eq 'restart') { Show-SetupRestart $View $Name; return $null }
    if ($state.NeedsRuntime) {
        $outcome = Install-WslRuntime $View $state.Executable
        if ($outcome -eq 'restart') { Show-SetupRestart $View $Name; return $null }
    }
    Save-SetupContinuation $Name 'ubuntu-installation'
    $outcome = Install-UbuntuForCurrentUser $View $Name
    if ($outcome -eq 'restart') { Show-SetupRestart $View $Name; return $null }
    Save-SetupContinuation $Name 'ubuntu-user-initialization'
    Initialize-UbuntuUser $View $Name
    $names = @(Get-InstalledDistributions)
    if ($Name -notin $names) { throw "Ubuntu installation did not register $Name for this Windows account. See setup-windows.log; setup has not been marked complete." }
    return $Name
}

function Get-InstalledDistributions {
    try { $output = Capture @('--list','--quiet') -Unicode }
    catch { throw ('Could not read installed WSL distributions. Details: ' + $_.Exception.Message) }
    return @($output -split "`r?`n" | ForEach-Object { $_.Trim().Trim([char]0xFEFF) } | Where-Object { $_ })
}
function Capture([string[]]$Arguments, [switch]$Unicode) {
    $executable = Get-WslExecutable
    if (-not $executable) { throw 'Windows WSL command is not installed.' }
    $task = Start-Owned $executable $Arguments -Unicode:$Unicode
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    while (-not $task.Process.WaitForExit(100)) {
        [Windows.Forms.Application]::DoEvents()
        if ($deadline.Elapsed.TotalSeconds -gt 45) { $task.Process.Kill(); throw 'Ubuntu did not respond. Open Ubuntu to finish initialization, then retry.' }
    }
    if ($Unicode) {
        $null = $task.Copy.GetAwaiter().GetResult()
        try { $output = Decode-WslOutput ($task.Bytes.ToArray()) } finally { $task.Bytes.Dispose() }
    } else { $output = $task.Out.Result.Trim().Replace([string][char]0, '') }
    if ($task.Process.ExitCode -ne 0) { throw ("WSL exit $($task.Process.ExitCode): " + $output + ' ' + $task.Err.Result) }
    return $output
}
function Write-Json([string]$Path, $Value) {
    $temporary = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.writing'
    try {
        [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 8 -Compress), $script:Utf8)
        $deadline = [Diagnostics.Stopwatch]::StartNew()
        while (-not [BactoFiles]::MoveFileEx($temporary, $Path, 1)) {
            $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            # WSL readers and scanners can briefly deny replacement on NTFS.
            # Keep the old complete JSON while retrying only sharing/access errors.
            if ($code -notin @(5,32,33) -or $deadline.Elapsed.TotalSeconds -ge 2) {
                throw "Could not update desktop control file '$Path' (Windows error $code)."
            }
            # No DoEvents here: a heartbeat callback must not reenter this write.
            Start-Sleep -Milliseconds 25
        }
    } finally { if ([IO.File]::Exists($temporary)) { Remove-Item -LiteralPath $temporary -ErrorAction SilentlyContinue } }
}
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class BactoFiles {
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 public static extern bool MoveFileEx(string from, string to, int flags);
}
'@
function Read-SharedText([string]$Path) {
    $stream = [IO.FileStream]::new($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try {
        $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8, $true)
        try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
    } finally { $stream.Dispose() }
}
function Read-Json([string]$Path) {
    try { return ((Read-SharedText $Path) | ConvertFrom-Json) }
    catch { return $null }
}
function Setup-Color([string]$Hex) { return [Drawing.ColorTranslator]::FromHtml($Hex) }
function New-SetupLabel([string]$Text, [single]$Size = 10, [switch]$Bold) {
    $label = New-Object Windows.Forms.Label
    $label.AutoSize = $true; $label.Text = $Text; $label.Margin = [Windows.Forms.Padding]::new(0,0,0,12)
    $label.Font = [Drawing.Font]::new('Segoe UI',$Size,$(if ($Bold) { [Drawing.FontStyle]::Bold } else { [Drawing.FontStyle]::Regular }))
    $label.ForeColor = Setup-Color '#17363D'
    return $label
}
function New-SetupButton([string]$Text, [switch]$Primary) {
    $button = New-Object Windows.Forms.Button
    $button.Text = $Text; $button.AutoSize = $true; $button.AutoSizeMode = 'GrowAndShrink'
    $button.MinimumSize = [Drawing.Size]::new(112,42); $button.Padding = [Windows.Forms.Padding]::new(16,6,16,6)
    $button.Margin = [Windows.Forms.Padding]::new(8,4,0,4); $button.FlatStyle = 'Flat'
    $button.Font = [Drawing.Font]::new('Segoe UI',10,[Drawing.FontStyle]::Bold)
    $button.FlatAppearance.BorderSize = 1; $button.FlatAppearance.BorderColor = Setup-Color '#CADBD9'
    $button.BackColor = $(if ($Primary) { Setup-Color '#11776F' } else { [Drawing.Color]::White })
    $button.ForeColor = $(if ($Primary) { [Drawing.Color]::White } else { Setup-Color '#17363D' })
    $button.UseVisualStyleBackColor = $false
    return $button
}
function Fit-SetupWindow($View) {
    $area = [Windows.Forms.Screen]::FromControl($View.Form).WorkingArea
    $maximum = [Drawing.Size]::new([Math]::Max(500,$area.Width-40),[Math]::Max(360,$area.Height-40))
    $View.Form.MinimumSize = [Drawing.Size]::new([Math]::Min($View.Form.MinimumSize.Width,$maximum.Width),[Math]::Min($View.Form.MinimumSize.Height,$maximum.Height))
    $View.Form.Size = [Drawing.Size]::new([Math]::Min($View.Form.Width,$maximum.Width),[Math]::Min($View.Form.Height,$maximum.Height))
    $View.Form.Location = [Drawing.Point]::new($area.Left+[int](($area.Width-$View.Form.Width)/2),$area.Top+[int](($area.Height-$View.Form.Height)/2))
}
function New-Progress([string]$Title) {
    $form = New-Object Windows.Forms.Form
    $form.Text = $Title; $form.ClientSize = [Drawing.Size]::new(940,660)
    $form.MinimumSize = [Drawing.Size]::new(760,500); $form.StartPosition = 'CenterScreen'
    $form.AutoScaleMode = 'Dpi'; $form.AutoScaleDimensions = [Drawing.SizeF]::new(96,96)
    $form.BackColor = Setup-Color '#F4F8F7'; $form.Font = [Drawing.Font]::new('Segoe UI',10)
    $icon = Join-Path $PSScriptRoot 'brand.ico'
    if (Test-Path -LiteralPath $icon) {
        $sourceIcon = [Drawing.Icon]::new($icon)
        try { $form.Icon = $sourceIcon.Clone() } finally { $sourceIcon.Dispose() }
    }
    $shell = New-Object Windows.Forms.TableLayoutPanel
    $shell.Dock = 'Fill'; $shell.ColumnCount = 2; $shell.RowCount = 1; $shell.Margin = [Windows.Forms.Padding]::Empty
    $null = $shell.ColumnStyles.Add([Windows.Forms.ColumnStyle]::new('Absolute',230))
    $null = $shell.ColumnStyles.Add([Windows.Forms.ColumnStyle]::new('Percent',100))
    $form.Controls.Add($shell)
    $sidebar = New-Object Windows.Forms.TableLayoutPanel
    $sidebar.Dock = 'Fill'; $sidebar.BackColor = Setup-Color '#112F37'
    $sidebar.Padding = [Windows.Forms.Padding]::new(24,28,20,22); $sidebar.ColumnCount = 1; $sidebar.RowCount = 4
    $null = $sidebar.RowStyles.Add([Windows.Forms.RowStyle]::new('AutoSize'))
    $null = $sidebar.RowStyles.Add([Windows.Forms.RowStyle]::new('AutoSize'))
    $null = $sidebar.RowStyles.Add([Windows.Forms.RowStyle]::new('Percent',100))
    $null = $sidebar.RowStyles.Add([Windows.Forms.RowStyle]::new('AutoSize'))
    $shell.Controls.Add($sidebar,0,0)
    $identity = New-Object Windows.Forms.FlowLayoutPanel
    $identity.FlowDirection = 'TopDown'; $identity.WrapContents = $false; $identity.AutoSize = $true; $identity.Dock = 'Top'
    $identity.Margin = [Windows.Forms.Padding]::new(0,0,0,28)
    $picture = New-Object Windows.Forms.PictureBox
    $picture.Size = [Drawing.Size]::new(52,58); $picture.SizeMode = 'Zoom'; $picture.Margin = [Windows.Forms.Padding]::new(0,0,0,14)
    $png = Join-Path $PSScriptRoot 'brand.png'
    if (Test-Path -LiteralPath $png) {
        $sourceImage = [Drawing.Image]::FromFile($png)
        try { $picture.Image = $sourceImage.Clone() } finally { $sourceImage.Dispose() }
    }
    $brand = New-SetupLabel '' 22 -Bold
    $brand.Text = 'Bactologix'; $brand.ForeColor = [Drawing.Color]::White; $brand.Margin = [Windows.Forms.Padding]::new(0,0,0,5)
    $brand.MaximumSize = [Drawing.Size]::new(186,0)
    $tag = New-SetupLabel 'YOUR WORKFLOW, CONNECTED' 8
    $tag.ForeColor = Setup-Color '#9AD7C7'; $tag.Margin = [Windows.Forms.Padding]::new(0,0,0,0)
    $identity.Controls.AddRange(@($picture,$brand,$tag)); $sidebar.Controls.Add($identity,0,0)
    $stepPanel = New-Object Windows.Forms.FlowLayoutPanel
    $stepPanel.FlowDirection = 'TopDown'; $stepPanel.WrapContents = $false; $stepPanel.AutoSize = $true; $stepPanel.Dock = 'Top'
    $stepLabels = @(); $stepNumbers = @()
    $names = @('Windows & Ubuntu','Conda','Workspace','Bactologix app','Desktop shortcut')
    for ($index=0; $index -lt $names.Count; $index++) {
        $row = New-Object Windows.Forms.TableLayoutPanel
        $row.AutoSize = $true; $row.ColumnCount = 2; $row.Margin = [Windows.Forms.Padding]::new(0,0,0,20)
        $null = $row.ColumnStyles.Add([Windows.Forms.ColumnStyle]::new('Absolute',30))
        $null = $row.ColumnStyles.Add([Windows.Forms.ColumnStyle]::new('Absolute',152))
        $number = New-SetupLabel ([string]($index+1)) 10 -Bold; $number.Margin = [Windows.Forms.Padding]::new(0,0,0,0)
        $label = New-SetupLabel $names[$index] 10; $label.MaximumSize = [Drawing.Size]::new(152,0); $label.Margin = [Windows.Forms.Padding]::new(0,0,0,0)
        $row.Controls.Add($number,0,0); $row.Controls.Add($label,1,0); $stepPanel.Controls.Add($row)
        $stepLabels += $label; $stepNumbers += $number
    }
    $sidebar.Controls.Add($stepPanel,0,1)
    $powered = New-SetupLabel 'Powered by GenoMix Human Link.' 9
    $powered.MaximumSize = [Drawing.Size]::new(180,0); $powered.ForeColor = Setup-Color '#AAC9CB'; $powered.Margin = [Windows.Forms.Padding]::Empty
    $sidebar.Controls.Add($powered,0,3)
    $content = New-Object Windows.Forms.TableLayoutPanel
    $content.Dock = 'Fill'; $content.ColumnCount = 1; $content.RowCount = 2; $content.Padding = [Windows.Forms.Padding]::new(28,26,28,20)
    $null = $content.RowStyles.Add([Windows.Forms.RowStyle]::new('Percent',100))
    $null = $content.RowStyles.Add([Windows.Forms.RowStyle]::new('AutoSize'))
    $shell.Controls.Add($content,1,0)
    $scroll = New-Object Windows.Forms.Panel
    $scroll.Dock = 'Fill'; $scroll.AutoScroll = $true; $scroll.Margin = [Windows.Forms.Padding]::Empty
    $content.Controls.Add($scroll,0,0)
    $body = New-Object Windows.Forms.FlowLayoutPanel
    $body.Dock = 'Top'; $body.FlowDirection = 'TopDown'; $body.WrapContents = $false; $body.AutoSize = $true; $body.Margin = [Windows.Forms.Padding]::Empty
    $scroll.Controls.Add($body)
    $eyebrow = New-SetupLabel 'SETUP' 9 -Bold; $eyebrow.ForeColor = Setup-Color '#11776F'
    $heading = New-SetupLabel 'Preparing your workspace' 23 -Bold; $heading.Name = 'SetupHeading'
    $detail = New-SetupLabel '' 10; $detail.Name = 'SetupDetail'; $detail.ForeColor = Setup-Color '#526B70'
    $body.Controls.AddRange(@($eyebrow,$heading,$detail))
    $status = New-Object Windows.Forms.FlowLayoutPanel
    $status.FlowDirection = 'TopDown'; $status.WrapContents = $false; $status.AutoSize = $true
    $status.BackColor = Setup-Color '#E9F4F0'; $status.Padding = [Windows.Forms.Padding]::new(18,18,18,18)
    $status.Margin = [Windows.Forms.Padding]::new(0,8,0,20)
    $statusTitle = New-SetupLabel 'Getting ready' 11 -Bold
    $bar = New-Object Windows.Forms.ProgressBar
    $bar.Height = 10; $bar.Style = 'Marquee'; $bar.MarqueeAnimationSpeed = 28; $bar.Margin = [Windows.Forms.Padding]::new(0,0,0,12)
    $steps = New-SetupLabel '' 10; $steps.ForeColor = Setup-Color '#526B70'; $steps.Margin = [Windows.Forms.Padding]::Empty
    $status.Controls.AddRange(@($statusTitle,$bar,$steps)); $body.Controls.Add($status)
    $diagnostic = New-Object Windows.Forms.TextBox
    $diagnostic.Multiline = $true; $diagnostic.ReadOnly = $true; $diagnostic.ScrollBars = 'Vertical'
    $diagnostic.Height = 120; $diagnostic.BackColor = [Drawing.Color]::White; $diagnostic.BorderStyle = 'FixedSingle'
    $diagnostic.Font = [Drawing.Font]::new('Segoe UI',9); $diagnostic.Visible = $false
    $diagnostic.AccessibleName = 'Setup error details'; $diagnostic.Margin = [Windows.Forms.Padding]::new(0,0,0,16)
    $body.Controls.Add($diagnostic)
    $footer = New-SetupLabel 'Existing samples, runs, outputs and databases are retained.' 9
    $footer.ForeColor = Setup-Color '#526B70'; $body.Controls.Add($footer)
    $actions = New-Object Windows.Forms.FlowLayoutPanel
    $actions.Dock = 'Fill'; $actions.AutoSize = $true; $actions.FlowDirection = 'RightToLeft'; $actions.WrapContents = $true
    $actions.Padding = [Windows.Forms.Padding]::new(0,14,0,0); $actions.Margin = [Windows.Forms.Padding]::Empty
    $primary = New-SetupButton 'Start setup' -Primary
    $secondary = New-SetupButton 'Close'
    $logs = New-SetupButton 'Open setup logs'; $logs.Enabled = Test-Path -LiteralPath $script:Root
    $actions.Controls.AddRange(@($primary,$secondary,$logs)); $content.Controls.Add($actions,0,1)
    $view = [pscustomobject]@{
        Form=$form; Heading=$heading; Detail=$detail; Bar=$bar; Steps=$steps; Footer=$footer
        Eyebrow=$eyebrow; StatusCard=$status; StatusTitle=$statusTitle; Diagnostic=$diagnostic
        Primary=$primary; Secondary=$secondary; Logs=$logs; Content=$content; Body=$body; Scroll=$scroll
        Sidebar=$sidebar; Brand=$brand; StepPanel=$stepPanel; StepLabels=$stepLabels; StepNumbers=$stepNumbers
        State='working'; ActiveStep=0; Outcome=$null; Action=$null; PrimaryAction='start'; AllowClose=$false; LogRoot=$script:Root; Picture=$picture
    }
    $form.Tag = $view; $scroll.Tag = $view
    $scroll.Add_SizeChanged({ param($sender,$eventArgs) Update-SetupLayout $sender.Tag })
    $form.Add_Shown({ param($sender,$eventArgs) Fit-SetupWindow $sender.Tag; Update-SetupLayout $sender.Tag })
    $primary.Add_Click({ param($sender,$eventArgs) $v = $sender.FindForm().Tag; $v.Action = $v.PrimaryAction })
    $secondary.Add_Click({ param($sender,$eventArgs) $v = $sender.FindForm().Tag; if ($v.AllowClose) { $v.Action = 'close' } })
    $logs.Add_Click({
        param($sender,$eventArgs)
        $v = $sender.FindForm().Tag
        try { Start-Process -FilePath (Join-Path $env:WINDIR 'explorer.exe') -ArgumentList (Quote-Argument $v.LogRoot) | Out-Null }
        catch { $v.Diagnostic.Text = $_.Exception.Message; $v.Diagnostic.Visible = $true }
    })
    $form.Add_FormClosing({
        param($sender,$eventArgs)
        $v = $sender.Tag
        if (-not $v.AllowClose) {
            $eventArgs.Cancel = $true
            $v.Footer.Text = 'Bactologix is still working. Wait for this step to finish; its progress will remain visible here.'
        } else { $v.Action = 'close' }
    })
    $form.Add_FormClosed({ param($sender,$eventArgs) if ($sender.Tag.Picture.Image) { $sender.Tag.Picture.Image.Dispose() } })
    Update-SetupLayout $view
    return $view
}
function Update-SetupLayout($View) {
    if (-not $View) { return }
    $width = [Math]::Max(260,$View.Scroll.ClientSize.Width-22)
    foreach ($control in @($View.Eyebrow,$View.Heading,$View.Detail,$View.Footer)) { $control.MaximumSize = [Drawing.Size]::new($width,0) }
    $View.StatusCard.Width = $width; $View.Diagnostic.Width = $width
    $innerWidth = [Math]::Max(210,$width-$View.StatusCard.Padding.Horizontal)
    foreach ($control in @($View.StatusTitle,$View.Steps)) { $control.MaximumSize = [Drawing.Size]::new($innerWidth,0) }
    $View.Bar.Width = $innerWidth
}
function Set-SetupSteps($View, [int]$Step) {
    $View.ActiveStep = [Math]::Max(0,[Math]::Min(5,$Step))
    for ($index=0; $index -lt $View.StepLabels.Count; $index++) {
        $done = $View.State -eq 'complete' -or ($View.ActiveStep -gt 0 -and $index+1 -lt $View.ActiveStep)
        $active = $index+1 -eq $View.ActiveStep -and $View.State -ne 'complete'
        $color = if ($done) { '#9AD7C7' } elseif ($active) { '#FFFFFF' } else { '#93AFB4' }
        $View.StepLabels[$index].ForeColor = Setup-Color $color
        $View.StepNumbers[$index].ForeColor = Setup-Color $color
        $View.StepNumbers[$index].Text = if ($done) { [string][char]0x2713 } else { [string]($index+1) }
    }
    if ($View.ActiveStep -gt 0) { $View.Steps.Text = "Step $($View.ActiveStep) of 5 - $($View.StepLabels[$View.ActiveStep-1].Text)" }
}
function Set-SetupPhase($View, [string]$State) {
    $View.State = $State; $View.Action = $null
    $busy = $State -in @('working','launcher')
    $script:AllowClose = -not $busy; $View.AllowClose = -not $busy
    $View.Primary.Visible = -not $busy; $View.Secondary.Visible = -not $busy
    $View.Logs.Enabled = Test-Path -LiteralPath $script:Root
    $View.Diagnostic.Visible = $State -eq 'error'
    $View.Bar.Visible = $busy -or $State -eq 'complete'
    $View.Bar.Style = if ($busy) { 'Marquee' } else { 'Continuous' }
    $View.Bar.Value = if ($State -eq 'complete') { 100 } else { 0 }
    $View.StatusCard.BackColor = Setup-Color $(if ($State -in @('restart','error','paused')) { '#FFF5E4' } else { '#E9F4F0' })
    $View.StepPanel.Visible = $State -ne 'launcher'
    $View.Eyebrow.Text = if ($State -eq 'launcher') { 'YOUR WORKSPACE' } else { 'SETUP' }
    $View.Footer.Text = if ($busy) { 'This can take several minutes. Keep this window open; downloads and checks may pause between updates.' } else { 'Existing samples, runs, outputs and databases are retained.' }
    Set-SetupSteps $View $View.ActiveStep
    if ($State -eq 'launcher') { $View.Steps.Text = 'Starting your saved workspace. Your existing data stays in place.' }
}
function Show-Stage($View, [string]$Title, [string]$Detail) {
    if ($View.State -notin @('working','launcher')) { Set-SetupPhase $View 'working' }
    $View.Heading.Text = $Title; $View.Detail.Text = $Detail
    $View.StatusTitle.Text = 'Working on this step'
    $View.Logs.Enabled = Test-Path -LiteralPath $script:Root
    if (-not $View.Form.Visible) { $View.Form.Show() }
    [Windows.Forms.Application]::DoEvents()
}
function Show-SetupWelcome($View) {
    Set-SetupPhase $View 'welcome'; Set-SetupSteps $View 0
    $pending = Read-Json (Join-Path $script:Root 'setup-state.json')
    $resume = $pending -and $pending.phase -ne 'complete'
    $View.Heading.Text = if ($resume) { 'Continue your setup' } else { 'Make room for discovery.' }
    $View.Detail.Text = 'Set up your Bactologix workspace in a few guided steps. Setup checks Windows and Ubuntu, prepares the application environment, and adds a desktop shortcut.'
    $View.StatusTitle.Text = if ($resume) { 'Your earlier progress is saved' } else { 'Ready when you are' }
    $View.Steps.Text = 'Internet is needed for verified application and prerequisite downloads. Windows may request administrator permission and a restart.'
    $View.Primary.Text = if ($resume) { 'Continue setup' } else { 'Start setup' }
    $View.PrimaryAction = 'start'; $View.Secondary.Text = 'Close'
    $View.Form.AcceptButton = $View.Primary; $View.Form.CancelButton = $View.Secondary
    if (-not $View.Form.Visible) { $View.Form.Show() }
    [Windows.Forms.Application]::DoEvents()
}
function Show-SetupOutcome($View, [string]$State, [string]$Title, [string]$Detail, [string]$Diagnostic = '') {
    Set-SetupPhase $View $State
    $View.Outcome = $State; $View.Heading.Text = $Title; $View.Detail.Text = $Detail; $View.Diagnostic.Text = $Diagnostic
    $View.Primary.Visible = $State -in @('complete','error')
    $View.Primary.Text = if ($State -eq 'complete') { 'Open Bactologix' } else { 'Try setup again' }
    $View.PrimaryAction = if ($State -eq 'complete') { 'open' } else { 'retry' }
    $View.Secondary.Text = if ($State -eq 'restart') { 'Close - restart later' } else { 'Close' }
    $View.StatusTitle.Text = switch ($State) { 'complete' { 'Setup complete' } 'restart' { 'Restart required' } 'paused' { 'No installation is running' } default { 'Your next step' } }
    $View.Steps.Text = switch ($State) {
        'complete' { 'Open the app now or use its desktop shortcut later. Pipeline setup and your saved work are available inside Bactologix.' }
        'restart' { 'Continue Bactologix Setup is saved on your desktop. Open it after Windows has restarted.' }
        'paused' { 'Close this window and return to Bactologix Setup when you are ready.' }
        default { 'Read the details below, open the setup logs, and retry after resolving the reported issue.' }
    }
    if ($State -eq 'complete') { Set-SetupSteps $View 5; $View.Steps.Text = 'Application verified and desktop shortcut created.' }
    $View.Form.AcceptButton = if ($View.Primary.Visible) { $View.Primary } else { $View.Secondary }
    $View.Form.CancelButton = $View.Secondary
    if (-not $View.Form.Visible) { $View.Form.Show() }
    [Windows.Forms.Application]::DoEvents()
}
function Wait-SetupAction($View) {
    while (-not $View.Action -and -not $View.Form.IsDisposed -and $View.Form.Visible) {
        [Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 80
    }
    return $(if ($View.Action) { $View.Action } else { 'close' })
}
function New-Shortcut([string]$Directory, [string]$Destination) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Destination)
    $shortcut.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
    $shortcut.Arguments = Quote-Argument (Join-Path $Directory 'Bactologix.vbs')
    $shortcut.WorkingDirectory = $Directory
    $shortcut.IconLocation = (Join-Path $Directory 'brand.ico') + ',0'
    $shortcut.Description = 'Bactologix - HybAs, BaSIS and GeneScope'
    $shortcut.Save()
}
function Find-Browser {
    foreach ($name in @('Microsoft\Edge\Application\msedge.exe','Google\Chrome\Application\chrome.exe')) {
        foreach ($base in @($env:ProgramFiles,${env:ProgramFiles(x86)},$env:LOCALAPPDATA)) {
            if ($base) { $path = Join-Path $base $name; if (Test-Path -LiteralPath $path) { return $path } }
        }
    }
    throw 'Install Microsoft Edge or Google Chrome before using the desktop launcher.'
}
function Browser-Endpoint([string]$Profile) {
    $file = Join-Path $Profile 'DevToolsActivePort'
    if (-not (Test-Path -LiteralPath $file)) { return $null }
    try { $lines = (Read-SharedText $file) -split "`r?`n" } catch { return $null }
    if ($lines.Length -lt 2 -or $lines[0] -notmatch '^\d{1,5}$' -or [int]$lines[0] -gt 65535 -or $lines[1] -notmatch '^/devtools/browser/[a-zA-Z0-9-]+$') { return $null }
    return @{ port=$lines[0]; socket=$lines[1] }
}
function Browser-State([string]$Profile) {
    $endpoint = Browser-Endpoint $Profile
    if (-not $endpoint) { return @{ reachable=$false; pages=0 } }
    try {
        $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$($endpoint.port)/json/list")
        $request.CachePolicy = [Net.Cache.RequestCachePolicy]::new([Net.Cache.RequestCacheLevel]::NoCacheNoStore)
        $request.Proxy = $null; $request.Timeout = 1000
        $response = $request.GetResponse()
        try {
            $reader = New-Object IO.StreamReader($response.GetResponseStream())
            $rows = $reader.ReadToEnd() | ConvertFrom-Json
        } finally { $response.Dispose() }
        return @{ reachable=$true; pages=@($rows | Where-Object { $_.type -eq 'page' }).Count }
    } catch { return @{ reachable=$false; pages=0 } }
}
function Update-BrowserPresence($Presence, $Observed, [double]$Seconds) {
    if ($Observed.reachable -and $Observed.pages -gt 0) {
        $Presence.ready = $true; $Presence.missing_since = $null
        return $false
    }
    if (-not $Presence.ready) { return $false }
    if ($null -eq $Presence.missing_since) { $Presence.missing_since = $Seconds }
    # A resize, minimization or busy browser is not a close event. Require a
    # sustained absence; the heartbeat continues during this grace period.
    return ($Seconds - $Presence.missing_since -ge 10)
}
function Send-BrowserCloseRequest($Endpoint) {
    $socket = New-Object Net.WebSockets.ClientWebSocket
    $cancel = New-Object Threading.CancellationTokenSource
    $cancel.CancelAfter(2000)
    $stage = 'connect'
    try {
        $socket.Options.Proxy = $null
        $null = $socket.ConnectAsync([Uri]"ws://127.0.0.1:$($Endpoint.port)$($Endpoint.socket)", $cancel.Token).GetAwaiter().GetResult()
        $stage = 'send'
        $bytes = [Text.Encoding]::UTF8.GetBytes('{"id":1,"method":"Browser.close"}')
        $segment = [ArraySegment[byte]]::new($bytes)
        $null = $socket.SendAsync($segment, [Net.WebSockets.WebSocketMessageType]::Text, $true, $cancel.Token).GetAwaiter().GetResult()
        $stage = 'acknowledgement'
        # Do not abort the connection immediately after sending: wait for the
        # browser to consume the request and acknowledge or close the socket.
        $buffer = New-Object byte[] 4096
        $reply = [ArraySegment[byte]]::new($buffer)
        $received = $socket.ReceiveAsync($reply, $cancel.Token).GetAwaiter().GetResult()
        Write-Verbose ('Browser close reply: ' + [Text.Encoding]::UTF8.GetString($buffer,0,$received.Count))
    } catch { throw ('Browser close ' + $stage + ': ' + $_.Exception.Message) } finally { $socket.Dispose(); $cancel.Dispose() }
}
function Close-OwnedBrowser([string]$Profile) {
    $endpoint = Browser-Endpoint $Profile
    if (-not $endpoint) { return }
    # A cancelled handshake does not establish delivery. Retry only this saved
    # browser endpoint, with a fresh socket/token each time; never target another
    # profile or terminate processes by browser name. Browser.close is idempotent.
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try { Send-BrowserCloseRequest $endpoint; return }
        catch { Write-Verbose ('Browser close attempt ' + $attempt + '/3: ' + $_.Exception.Message) }
        if ($attempt -lt 3) { Start-Sleep -Milliseconds 250 }
    }
}

function Save-Log($Task, [string]$Path) {
    if ($Task -and $Task.Process.HasExited) { [IO.File]::WriteAllText($Path, $Task.Out.Result + $Task.Err.Result, $script:Utf8) }
}
function Install-Desktop($View) {
    Set-SetupPhase $View 'working'; Set-SetupSteps $View 1
    Show-Stage $View 'Checking Windows and Ubuntu' 'Checking the installed WSL distributions. Administrator prompts appear only when Windows needs them.'
    Save-SetupFiles
    $prepared = Prepare-Ubuntu $View $Distro
    if (-not $prepared) { return }
    $Distro = $prepared
    Test-UbuntuReady $Distro
    if ((Capture @('--distribution',$Distro,'--exec','id','-u')) -eq '0') {
        throw 'Ubuntu is using root. Open Ubuntu and finish creating your regular Linux user, then rerun Setup. Bactologix is installed as that user.'
    }
    Save-SetupContinuation $Distro 'application-installation'
    Show-Stage $View 'Ubuntu is ready' "Using $Distro as your regular Linux user. Checking prerequisites."
    $null = Find-Browser
    try { $null = Capture @('--distribution',$Distro,'--exec','python3','--version') }
    catch { throw 'Ubuntu needs Python 3. Open Ubuntu and run: sudo apt update && sudo apt install -y python3 git ca-certificates . Then rerun Setup.' }
    $linuxRoot = Capture @('--distribution',$Distro,'--exec','wslpath','-u',$script:Root)
    $result = Join-Path $script:Root 'setup-result.json'
    Remove-Item -LiteralPath $result -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $script:Root 'setup-status.json') -ErrorAction SilentlyContinue
    $task = Start-Owned 'wsl.exe' @('--distribution',$Distro,'--exec','python3',"$linuxRoot/install_bactologix.py",'--install-conda','--upgrade-app','--no-launch','--status-file',"$linuxRoot/setup-status.json",'--result-file',"$linuxRoot/setup-result.json")
    try {
        while (-not $task.Process.WaitForExit(200)) {
            $status = Read-Json (Join-Path $script:Root 'setup-status.json')
            if ($status) {
                Show-Stage $View $status.title $status.detail
                # Native step 5 reports application readiness; Windows shortcut setup is still pending.
                Set-SetupSteps $View ([Math]::Min(4,[int]$status.step))
            }
            [Windows.Forms.Application]::DoEvents()
        }
    } finally { Save-Log $task (Join-Path $script:Root 'setup.log') }
    if ($task.Process.ExitCode -ne 0) { throw "Application setup stopped. See $script:Root\setup.log for details." }
    $installed = Read-Json $result
    if (-not $installed.python) { throw 'The installer did not record a Python environment.' }
    $null = Capture @('--distribution',$Distro,'--exec',$installed.python,'-c','import bactologix.desktop')
    Write-Json (Join-Path $script:Root 'installation.json') @{ distro=$Distro; python=$installed.python }
    Set-SetupSteps $View 5
    Show-Stage $View 'Adding your desktop shortcut' 'The application passed its import check. Creating the Windows shortcut and saving setup completion.'
    New-Shortcut $script:Root (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Bactologix.lnk')
    Write-Json (Join-Path $script:Root 'setup-state.json') @{ distro=$Distro; phase='complete' }
    Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Continue Bactologix Setup.lnk') -ErrorAction SilentlyContinue
    Show-SetupOutcome $View 'complete' 'Your workspace is ready.' 'Bactologix is installed and its desktop shortcut is ready. Open it now, or return whenever you are ready to work.'
}
function Run-Desktop($View) {
    Set-SetupPhase $View 'launcher'
    $installed = Read-Json (Join-Path $script:Root 'installation.json')
    if (-not $installed.python -or -not $installed.distro) { throw 'Run Bactologix Setup first to create the desktop installation.' }
    $browser = Find-Browser
    $id = [Guid]::NewGuid().ToString('N')
    $directory = Join-Path $script:Root "sessions\$id"
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $linuxDirectory = Capture @('--distribution',$installed.distro,'--exec','wslpath','-u',$directory)
    $heartbeat = { Write-Json (Join-Path $directory 'heartbeat.json') @{session=$id; time=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()} }
    $request = { param($action) Write-Json (Join-Path $directory 'request.json') @{session=$id; action=$action; nonce=[Guid]::NewGuid().ToString('N')} }
    & $heartbeat
    $task = Start-Owned 'wsl.exe' @('--distribution',$installed.distro,'--exec',$installed.python,'-m','bactologix.cli','--desktop-control',$linuxDirectory,'--desktop-session',$id,'--no-browser')
    $window = $null; $opened = $false; $closing = $false; $browserRequest = $null; $asked = $false
    $profile = Join-Path $directory 'browser'
    $browserReady = $false; $browserClock = $null
    $clock = [Diagnostics.Stopwatch]::StartNew()
    Show-Stage $View 'Opening your workspace' 'Starting Bactologix inside Ubuntu. No terminal needs to stay open.'
    try {
        while (-not $task.Process.WaitForExit(250)) {
            & $heartbeat
            $status = Read-Json (Join-Path $directory 'status.json')
            if (-not $status -or $status.session -ne $id) {
                if ($clock.Elapsed.TotalSeconds -gt 90) { throw 'Desktop startup did not respond. The supervisor will request cancellation; check the session log.' }
                [Windows.Forms.Application]::DoEvents(); continue
            }
            if ($status.phase -eq 'ready' -and -not $opened) {
                $uri = [Uri]$status.url
                if ($uri.Scheme -ne 'http' -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment -or $uri.UserInfo) { throw 'Invalid local application address.' }
                $window = Start-Owned $browser @("--user-data-dir=$profile",'--no-first-run','--no-default-browser-check','--disable-background-mode','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',"--app=$($status.url)",'--window-size=1440,1000')
                $opened = $true; $closing = $false; $browserReady = $false
                $presence = @{ ready=$false; missing_since=$null }
                $browserClock = [Diagnostics.Stopwatch]::StartNew(); $View.Form.Hide()
            }
            if ($status.browser_request -and $status.browser_request -ne $browserRequest) {
                $browserRequest = $status.browser_request
                $null = Start-Owned $browser @("--user-data-dir=$profile",'--disable-background-mode','--new-window',$status.url)
            }
            if ($opened -and -not $closing) {
                $browserState = Browser-State $profile
                $browserClosed = Update-BrowserPresence $presence $browserState $browserClock.Elapsed.TotalSeconds
                $browserReady = $presence.ready
                if ($browserClosed) {
                    Close-OwnedBrowser $profile
                    & $request 'close'; $closing = $true
                } elseif (-not $browserReady -and $browserClock.Elapsed.TotalSeconds -gt 30) {
                    throw 'The dedicated browser did not become ready. Check local browser policies; this session will stop.'
                }
            }
            if ($status.phase -eq 'ready') { $asked = $false }
            if ($status.phase -eq 'confirming' -and -not $asked) {
                $asked = $true
                # Keep the lease alive while the native dialog is open.
                $timer = New-Object Windows.Forms.Timer; $timer.Interval = 1000
                $timer.Add_Tick($heartbeat); $timer.Start()
                try { $answer = [Windows.Forms.MessageBox]::Show("$($status.jobs) job(s) belong to this window. Stop them and exit? Completed work is retained. Select No to reopen Bactologix.", 'Stop analyses and exit?', 'YesNo', 'Question') }
                finally { $timer.Stop(); $timer.Dispose() }
                if ($answer -eq 'Yes') { & $request 'confirm_close' }
                else { & $request 'reopen'; $opened = $false }
            }
            if ($status.phase -eq 'stopping') {
                $message = if ($status.installing_update) { 'Finishing the application update safely before exit.' } else { 'Stopping only this session''s jobs and allowing cleanup. Other Ubuntu work stays running.' }
                Show-Stage $View 'Closing Bactologix' $message
            }
            [Windows.Forms.Application]::DoEvents()
        }
    } finally {
        if (-not $task.Process.HasExited -and -not (Read-Json (Join-Path $directory 'status.json'))) {
            $task.Process.Kill() # Only our Windows startup client; no UI or jobs were admitted.
        }
        if (-not $task.Process.HasExited) {
            & $request 'confirm_close'
            # Keep the supervisor visible until owned work has actually stopped.
            Show-Stage $View 'Stopping this session' 'Waiting for owned jobs to clean up. Other Ubuntu processes are untouched.'
            while (-not $task.Process.WaitForExit(250)) { & $heartbeat; [Windows.Forms.Application]::DoEvents() }
        }
        Save-Log $task (Join-Path $directory 'launcher.log')
        Close-OwnedBrowser $profile
        if ($window -and -not $window.Process.HasExited) { $null = $window.Process.CloseMainWindow() }
    }
    if ($task.Process.ExitCode -ne 0) { throw "Bactologix stopped unexpectedly. See $directory\launcher.log ." }
}
function Invoke-SetupInterface($View) {
    Show-SetupWelcome $View
    if ((Wait-SetupAction $View) -ne 'start') { return 0 }
    while ($true) {
        $View.Outcome = $null
        try { Install-Desktop $View }
        catch {
            Show-SetupOutcome $View 'error' 'Setup needs your attention' 'Setup stopped before it could finish. Your saved work remains in place. Review the details and setup logs before trying again.' $_.Exception.Message
        }
        if (-not $View.Outcome) {
            Show-SetupOutcome $View 'error' 'Setup did not finish' 'A completion record was not produced. Review the setup logs before trying again.' 'Setup returned without recording completion or a restart requirement.'
        }
        $code = switch ($View.Outcome) { 'complete' { 0 } 'restart' { 3010 } 'paused' { 1223 } default { 1 } }
        $action = Wait-SetupAction $View
        if ($action -eq 'retry' -and $View.Outcome -eq 'error') { continue }
        if ($action -eq 'open' -and $View.Outcome -eq 'complete') {
            $View.Form.Hide()
            Run-Desktop $View
        }
        return $code
    }
}
if (-not $TestMode) {
    $script:AllowClose = $false
    $view = New-Progress $(if ($Setup) { 'Bactologix Setup' } else { 'Bactologix' })
    $exitCode = 0
    try {
        if ($Setup) { $exitCode = Invoke-SetupInterface $view }
        else { Run-Desktop $view }
    } catch {
        $exitCode = 1
        Show-SetupOutcome $view 'error' 'Bactologix needs your attention' 'The workspace could not open or close normally. Review the details below and the recorded log location.' $_.Exception.Message
        $view.Primary.Visible = $false
        $null = Wait-SetupAction $view
    } finally {
        $script:AllowClose = $true; $view.AllowClose = $true
        if (-not $view.Form.IsDisposed) { $view.Form.Close(); $view.Form.Dispose() }
    }
    exit $exitCode
}
