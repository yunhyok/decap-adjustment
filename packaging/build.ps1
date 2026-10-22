[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$Iscc = 'C:\Program Files\Inno Setup 7\ISCC.exe'
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath $Iscc)) { throw "Inno Setup 7 compiler not found: $Iscc" }
    New-Item -ItemType Directory -Force -Path 'work', 'outputs' | Out-Null
    $buildPython = Join-Path $repoRoot 'work\build-venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $buildPython)) {
        & $Python -c "import sys,struct; assert sys.version_info[:3] == (3,12,10) and struct.calcsize('P') == 8, 'Use CPython 3.12.10 x64'"
        if ($LASTEXITCODE) { throw 'CPython 3.12.10 x64 is required for this release build.' }
        & $Python -m venv 'work\build-venv'
        if ($LASTEXITCODE) { throw 'Could not create build environment.' }
    }
    & $buildPython -c "import sys,struct; assert sys.version_info[:3] == (3,12,10) and struct.calcsize('P') == 8"
    if ($LASTEXITCODE) { throw 'The existing build environment is not CPython 3.12.10 x64.' }
    & $buildPython -m pip install --disable-pip-version-check -r requirements-build.txt
    if ($LASTEXITCODE) { throw 'Dependency installation failed.' }
    & $buildPython -m pytest -q
    if ($LASTEXITCODE) { throw 'Tests failed; installer was not built.' }
    & $buildPython -m PyInstaller --noconfirm --clean --workpath work\pyinstaller --distpath work\dist DecapManager.spec
    if ($LASTEXITCODE) { throw 'PyInstaller failed.' }
    $version = & $buildPython -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
    if ($LASTEXITCODE -or $version -notmatch '^\d+\.\d+\.\d+$') { throw 'Expected a numeric major.minor.patch version.' }
    & $Iscc /Qp "/DAppVersion=$version" "/DSourceRoot=$repoRoot" (Join-Path $PSScriptRoot 'installer.iss')
    if ($LASTEXITCODE) { throw 'Installer compilation failed.' }
    $installer = Join-Path $repoRoot "outputs\DecapAdjustment-$version-Setup-x64.exe"
    $hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($installer))" | Set-Content -LiteralPath "$installer.sha256" -Encoding ascii
    & (Join-Path $PSScriptRoot 'verify-install.ps1') -Installer $installer
    Write-Host "Verified installer: $installer"
} finally {
    Pop-Location
}
