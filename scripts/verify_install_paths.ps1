[CmdletBinding()]
param([Parameter(Mandatory)][string]$Installer)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Production AppIds are safe only on a disposable GitHub-hosted runner.
if ($env:GITHUB_ACTIONS -ne 'true' -or -not $env:RUNNER_TEMP) {
    throw 'Run production installer lifecycle verification only on a disposable CI runner.'
}
$meta = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'installer-paths.json') -Raw | ConvertFrom-Json
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
if ($meta.exe -eq 'R2R Machine Learning.exe' -and $installerPath -notlike '*-cpu-*') {
    $meta.previousAsset = $meta.previousAsset.Replace('-cpu-', '-')
}
$root = Join-Path $env:RUNNER_TEMP ('installer-paths-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$key = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{' + $meta.appId + '}_is1'
$hklm = 'HKLM:\' + $key
$hkcu = 'HKCU:\' + $key
if ((Test-Path $hklm) -or (Test-Path $hkcu)) { throw 'Refusing to overwrite an existing application.' }
function Run([string]$File, [string[]]$Arguments) {
    $p = Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    if (-not $p.WaitForExit(900000)) { $p.Kill(); throw "Process timeout: $File" }
    if ($p.ExitCode -ne 0) { throw "Process failed ($($p.ExitCode)): $File" }
}
function Install([string]$File, [string[]]$Options, [string]$Log) {
    Run $File (@('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/TASKS=', "/LOG=`"$root\$Log.log`"") + $Options)
}
function Assert-App([string]$Path) {
    $exe = Join-Path $Path $meta.exe
    if (-not (Test-Path -LiteralPath $exe)) { throw "Installed executable missing: $exe" }
    if ($meta.exe -eq 'DecapAdjustment.exe') {
        Run $exe @('--self-test', "`"$root\self-test.json`"")
        if (-not (Get-Content "$root\self-test.json" -Raw | ConvertFrom-Json).ok) { throw 'Application self-test failed.' }
    } else { Run $exe @('--self-test') }
}
function Uninstall([string]$Path) {
    Run (Join-Path $Path 'unins000.exe') @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
    if (Test-Path -LiteralPath (Join-Path $Path $meta.exe)) { throw 'Uninstall left application executable.' }
}
# Test the exact asset that will be published, without a rebuilt verification AppId.
Install $installerPath @('/ALLUSERS') 'fresh-default'
$default = (Get-ItemProperty -LiteralPath $hklm).'Inno Setup: App Path'
if (-not $default.StartsWith($env:ProgramFiles + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Fresh all-users destination is outside Program Files: $default"
}
Assert-App $default
Uninstall $default

# A different drive letter plus spaces verifies that /DIR reaches the payload.
$drive = 'R:'
if (Test-Path "$drive\") { throw 'Verification drive letter is already in use.' }
& subst $drive $root
if ($LASTEXITCODE -ne 0) { throw 'Could not create verification drive.' }
try {
    $alternate = "$drive\Alternate Apps\Application"
    Install $installerPath @('/ALLUSERS', "/DIR=`"$alternate`"") 'alternate-drive'
    Assert-App $alternate
    Uninstall $alternate
} finally { & subst $drive /D }

# Exercise a real previously published per-user installer, not a synthetic version.
& gh release download $meta.previousTag --repo $env:GITHUB_REPOSITORY --pattern $meta.previousAsset --dir $root
if ($LASTEXITCODE -ne 0) { throw 'Previous release download failed.' }
$checksum = if ($meta.previousAsset -like 'R2RTxtConverter-*') { 'SHA256SUMS.txt' } else { $meta.previousAsset + '.sha256' }
& gh release download $meta.previousTag --repo $env:GITHUB_REPOSITORY --pattern $checksum --dir $root
if ($LASTEXITCODE -ne 0) { throw 'Previous release checksum download failed.' }
$line = Get-Content -LiteralPath (Join-Path $root $checksum) | Where-Object { $_ -match [regex]::Escape($meta.previousAsset) } | Select-Object -First 1
if (-not $line -or $line -notmatch '^([0-9a-fA-F]{64})\s+') { throw 'Previous release checksum is malformed.' }
if ((Get-FileHash -LiteralPath (Join-Path $root $meta.previousAsset) -Algorithm SHA256).Hash -ne $Matches[1]) { throw 'Previous release checksum mismatch.' }
$legacy = Join-Path $root 'Legacy App'
$moved = Join-Path $root 'Moved App'
Install (Join-Path $root $meta.previousAsset) @('/CURRENTUSER', "/DIR=`"$legacy`"") 'previous-release'
Set-Content -LiteralPath (Join-Path $legacy 'legacy-marker.txt') -Value 'preserve-user-data' -Encoding ascii
# No scope override: UsePreviousPrivileges must retain HKCU and the original path.
Install $installerPath @() 'upgrade-existing'
if (Test-Path $hklm) { throw 'Legacy upgrade unexpectedly changed to all-users scope.' }
if ((Get-ItemProperty -LiteralPath $hkcu).'Inno Setup: App Path' -ne $legacy) { throw 'Legacy path was not retained.' }
Assert-App $legacy
Install $installerPath @('/CURRENTUSER', "/DIR=`"$moved`"") 'upgrade-new-destination'
Assert-App $moved
if ((Get-Content -LiteralPath (Join-Path $legacy 'legacy-marker.txt') -Raw).Trim() -ne 'preserve-user-data') {
    throw 'Changing the destination damaged previous user data.'
}
Uninstall $moved
Uninstall $legacy
if (-not (Test-Path -LiteralPath (Join-Path $legacy 'legacy-marker.txt'))) { throw 'Uninstall removed personal data.' }
$report = [ordered]@{
    ok = $true; version = $meta.version; previousTag = $meta.previousTag
    installer = [IO.Path]::GetFileName($installerPath)
    sha256 = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    defaultProgramFiles = $default; alternateDriveAndSpaces = 'passed'
    actualPreviousReleaseUpgrade = 'passed'; previousUserDataPreserved = 'passed'
    changedUpgradeDestination = 'passed'; uninstall = 'passed'
}
$report | ConvertTo-Json | Set-Content -LiteralPath "$installerPath.verification.json" -Encoding utf8
$report | ConvertTo-Json
