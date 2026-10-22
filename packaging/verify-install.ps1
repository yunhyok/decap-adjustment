[CmdletBinding()]
param([Parameter(Mandatory)][string]$Installer)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$checkId = [Guid]::NewGuid().ToString('N')
$checkRoot = Join-Path $repoRoot "work\installer-check-$checkId"
$installDir = Join-Path $checkRoot 'app'
$groupName = 'Decap Adjustment'
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Decap Adjustment.lnk'
$startLink = Join-Path ([Environment]::GetFolderPath('Programs')) "$groupName\Decap Adjustment.lnk"
$registryPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{EA39DB72-3DEB-4D60-813E-BDE33AE92D66}_is1'
if ((Test-Path -LiteralPath $registryPath) -or (Test-Path -LiteralPath $desktopLink) -or (Test-Path -LiteralPath $startLink)) {
    throw 'An existing Decap Adjustment installation or shortcut exists. Verification refuses to overwrite it.'
}
New-Item -ItemType Directory -Path $checkRoot | Out-Null
function Invoke-CheckedProcess([string]$File, [string[]]$Arguments) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(120000)) {
        $process.Kill()
        throw "Process timed out: $File"
    }
    if ($process.ExitCode -ne 0) { throw "Process failed ($($process.ExitCode)): $File" }
}
$uninstaller = Join-Path $installDir 'unins000.exe'
$installed = $false
try {
    Invoke-CheckedProcess $installerPath @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
        "/DIR=`"$installDir`"", '/TASKS=desktopicon', "/LOG=`"$checkRoot\install.log`"")
    $installed = $true
    $executable = Join-Path $installDir 'DecapAdjustment.exe'
    foreach ($path in @($executable, $uninstaller, $desktopLink, $startLink)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Installation output missing: $path" }
    }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($link in @($desktopLink, $startLink)) {
        $shortcut = $shell.CreateShortcut($link)
        if ($shortcut.TargetPath -ne $executable -or $shortcut.IconLocation -notlike "$executable,*") {
            throw "Incorrect shortcut target/icon: $link"
        }
    }
    $entry = Get-ItemProperty -LiteralPath $registryPath
    if ($entry.DisplayName -notlike 'Decap Adjustment*' -or $entry.DisplayIcon -ne $executable) {
        throw 'Uninstall registry entry has incorrect name/icon.'
    }
    foreach ($mode in @('self-test', 'smoke-test')) {
        $report = Join-Path $checkRoot "$mode.json"
        Invoke-CheckedProcess $executable @("--$mode", "`"$report`"")
        if (-not (Test-Path -LiteralPath $report)) { throw "Missing $mode report." }
        $result = Get-Content -Raw -LiteralPath $report | ConvertFrom-Json
        if (-not $result.ok) { throw "$mode failed." }
    }
} finally {
    if (Test-Path -LiteralPath $uninstaller) {
        Invoke-CheckedProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
            "/LOG=`"$checkRoot\uninstall.log`"")
    }
}
if (-not $installed) { throw 'Installation was not completed.' }
foreach ($path in @((Join-Path $installDir 'DecapAdjustment.exe'), $desktopLink, $startLink, $registryPath)) {
    if (Test-Path -LiteralPath $path) { throw "Uninstall left an installed item: $path" }
}
$report = [ordered]@{
    ok = $true
    installer = [IO.Path]::GetFileName($installerPath)
    sha256 = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    install = 'passed'
    applicationSelfTest = 'passed'
    tkinterStartup = 'passed'
    desktopAndStartMenuIcons = 'passed'
    uninstall = 'passed'
}
New-Item -ItemType Directory -Force -Path (Join-Path $repoRoot 'outputs') | Out-Null
$report | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $repoRoot 'outputs\installer-verification.json') -Encoding utf8
Write-Host 'Installation, executable checks, shortcut icons, and uninstall passed.'
