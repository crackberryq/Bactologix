$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class BactologixDiskCapacity {
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 public static extern bool MoveFileEx(string from, string to, int flags);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 public static extern bool GetDiskFreeSpaceEx(string path, out ulong available, out ulong total, out ulong free);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 public static extern bool GetVolumePathName(string path, StringBuilder volume, uint length);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 public static extern bool GetVolumeNameForVolumeMountPoint(string path, StringBuilder volume, uint length);
}
'@ | Out-Null

function Get-WindowsStorageCapacity($payload) {
$errors = @()
$targets = @()
$swapRequired = $null
if ($payload.operation -eq 'discover') {
 try {
  $keys = @(Get-ChildItem -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' |
   Where-Object { $_.GetValue('DistributionName') -ceq $payload.distribution })
  if ($keys.Count -ne 1) { throw 'Current distribution registration is missing or ambiguous.' }
  $base = [string]$keys[0].GetValue('BasePath')
  if (-not $base -or -not [IO.Directory]::Exists($base)) { throw 'Current distribution storage directory is unavailable.' }
  $targets += @{ path=$base; label=('Windows storage for ' + $payload.distribution); role='wsl_backing' }
 } catch { $errors += ('Cannot identify WSL backing storage: ' + $_.Exception.Message) }
 if (-not $payload.backing_only) {
 try {
  $swap = $null; $swapFile = $null; $section = ''
  $config = Join-Path $env:USERPROFILE '.wslconfig'
  if ([IO.File]::Exists($config)) {
   if ((Get-Item -LiteralPath $config).Length -gt 65536) { throw 'WSL configuration exceeds the bounded read limit.' }
   foreach ($line in [IO.File]::ReadAllLines($config)) {
    $text = $line.Trim()
    if ($text -match '^\[([^\]]+)\]\s*$') { $section = $Matches[1].ToLowerInvariant(); continue }
    if ($section -ne 'wsl2' -or $text.StartsWith('#') -or $text.StartsWith(';')) { continue }
    if ($text -match '^swap\s*=\s*(.*?)\s*$') { $swap = $Matches[1] }
    if ($text -match '^swapfile\s*=\s*(.*?)\s*$') { $swapFile = $Matches[1] }
   }
  }
  $swapRequired = $null -eq $swap -or $swap -notmatch '^0+(?:B|KB|MB|GB|TB)?$'
  if ($swapRequired) {
   if (-not $swapFile) { $swapFile = Join-Path ([IO.Path]::GetTempPath()) 'swap.vhdx' }
   else {
    if (($swapFile.StartsWith('"') -and $swapFile.EndsWith('"')) -or ($swapFile.StartsWith("'") -and $swapFile.EndsWith("'"))) {
     $swapFile = $swapFile.Substring(1, $swapFile.Length - 2)
    }
    $swapFile = $swapFile.Replace('\\', '\').Replace('/', '\')
   }
   if ($swapFile -notmatch '^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+\\)') { throw 'Swap path is not an absolute Windows path.' }
   $targets += @{ path=$swapFile; label='Configured Windows swap storage'; role='wsl_swap' }
  }
 } catch { $errors += ('Cannot identify WSL swap storage: ' + $_.Exception.Message) }
 }
} elseif ($payload.operation -eq 'sample') { $targets = @($payload.targets) }
else { throw 'Unknown capacity probe operation.' }
$volumes = @()
foreach ($target in $targets) {
 try {
  $path = [string]$target.path
  if ($target.probe_path) {
   $existing = [string]$target.probe_path
   if (-not [IO.Directory]::Exists($existing)) { throw 'Previously identified storage directory is unavailable.' }
  } else {
   $existing = $path
   if ([IO.File]::Exists($existing)) { $existing = [IO.Path]::GetDirectoryName($existing) }
   while (-not [IO.Directory]::Exists($existing)) {
    $parent = [IO.Path]::GetDirectoryName($existing.TrimEnd('\'))
    if (-not $parent -or $parent -eq $existing) { throw 'No accessible directory for storage path.' }
    $existing = $parent
   }
  }
  [UInt64]$available = 0; [UInt64]$total = 0; [UInt64]$free = 0
  if (-not [BactologixDiskCapacity]::GetDiskFreeSpaceEx($existing, [ref]$available, [ref]$total, [ref]$free)) {
   throw ('Windows free-space query failed (' + [Runtime.InteropServices.Marshal]::GetLastWin32Error() + ').')
  }
  $mount = New-Object Text.StringBuilder 32768
  if ([BactologixDiskCapacity]::GetVolumePathName($existing, $mount, [UInt32]$mount.Capacity)) { $volumePath = $mount.ToString() }
  else { $volumePath = [IO.Path]::GetPathRoot($existing) }
  $name = New-Object Text.StringBuilder 1024
  if ([BactologixDiskCapacity]::GetVolumeNameForVolumeMountPoint($volumePath, $name, [UInt32]$name.Capacity)) { $identity = $name.ToString() }
  else { $identity = $volumePath }
  if (-not $identity -or $total -eq 0) { throw 'Windows storage identity or capacity is unavailable.' }
  $volumes += @{ id=('windows:' + $identity.ToLowerInvariant()); label=$target.label; path=$path;
   probe_path=$existing; volume_path=$volumePath; role=$target.role; kind='windows_host';
   available_bytes=$available; total_bytes=$total }
 } catch { $errors += ([string]$target.label + ': ' + $_.Exception.Message) }
}
return @{ volumes=@($volumes); errors=@($errors); swap_required=$swapRequired }
}

# The Linux caller substitutes base64 JSON. Sourcing the installed template
# from the native desktop exposes only the same fixed capacity function.
$encodedCapacityPayload = '__PAYLOAD__'
if (-not $encodedCapacityPayload.StartsWith('_')) {
 [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
 $payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedCapacityPayload)) | ConvertFrom-Json
 $record = Get-WindowsStorageCapacity $payload
 [Console]::WriteLine('BACTOLOGIX_CAPACITY_V1:' + ($record | ConvertTo-Json -Depth 5 -Compress))
}
