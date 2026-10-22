# Windows packaging

Use Windows 10/11 x64, CPython **3.12.10 x64**, and the tested **Inno Setup
7.0.1-beta x64** compiler. Application dependencies and build/test tools are pinned
in `requirements-build.txt`. The workflow downloads that exact compiler and verifies
its SHA-256 against the official release asset digest.

Run in PowerShell from the repository root:

```powershell
./packaging/build.ps1
```

This creates an isolated environment under `work/`, runs all tests, packages the
application, compiles the installer into `outputs/`, computes SHA-256, installs
into a unique test directory, executes the frozen self-check and Tk startup check,
checks desktop/start-menu shortcut targets and icons, then uninstalls. It refuses
to overwrite a pre-existing installation or desktop shortcut during verification.
Build logs and test installation directories stay under ignored `work/`.

Compiler override: `./packaging/build.ps1 -Iscc 'C:\path\ISCC.exe'`.
Python override: `./packaging/build.ps1 -Python 'C:\path\python.exe'`.

The installer is per-user and requires no administrator access. Its default
location is `%LOCALAPPDATA%\Programs\Decap Adjustment`. Users do not need Python
or Excel installed. Remove it through Windows Installed apps.

Git tags matching `v*` run `.github/workflows/windows-release.yml`. A tag must match
the version in `pyproject.toml`; the workflow creates a GitHub Release only after
tests, packaging, installation, frozen executable checks, and removal pass.
It publishes the installer, SHA-256 file, and concise verification report.

Reproducible here means documented, pinned build inputs and repeatable steps;
installer timestamps and Windows toolchain details can change binary bytes.
The artifacts are **unsigned** because no signing certificate is supplied;
Windows SmartScreen may show an unrecognized-app prompt. The compiler used for
this version is an Inno Setup beta and is explicitly pinned rather than silently
updated. Windows on ARM and other Windows versions are not validated.
