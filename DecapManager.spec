# Build from the repository root with packaging/build.ps1.
import sys
import tomllib
from importlib.metadata import distribution
from pathlib import Path
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct, VSVersionInfo,
)

root = Path(SPECPATH)
version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
version_tuple = tuple(int(part) for part in version.split('.')) + (0,)
datas = [(str(root / 'assets'), 'assets'),
         (str(root / 'LICENSE'), '.'), (str(root / 'README.md'), '.'),
         (str(root / 'packaging' / 'THIRD_PARTY_NOTICES.md'), '.')]
for filename in ('refdes.xlsx', 'distance.xlsx', 'commands.xlsx', 'commands_tall.xlsx'):
    datas.append((str(root / 'examples' / filename), 'examples'))
for package in ('openpyxl', 'et_xmlfile', 'pillow', 'pyinstaller'):
    dist = distribution(package)
    for file in dist.files or []:
        if any(word in str(file).upper() for word in ('LICEN', 'COPYING')):
            datas.append((str(dist.locate_file(file)), 'licenses/' + package))
datas.append((str(Path(sys.base_prefix) / 'LICENSE.txt'), 'licenses/python'))
for folder in ('tcl8.6', 'tk8.6'):
    license_file = Path(sys.base_prefix) / 'tcl' / folder / 'license.terms'
    if license_file.exists():
        datas.append((str(license_file), 'licenses/' + folder))

a = Analysis([str(root / 'run_app.py')], pathex=[str(root)], binaries=[],
             datas=datas, hiddenimports=[], hookspath=[], runtime_hooks=[],
             excludes=['pytest'], noarchive=False)
pyz = PYZ(a.pure)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple,
                     mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('FileDescription', 'Decap Adjustment'),
        StringStruct('FileVersion', version),
        StringStruct('ProductName', 'Decap Adjustment'),
        StringStruct('ProductVersion', version),
        StringStruct('OriginalFilename', 'DecapAdjustment.exe'),
        StringStruct('LegalCopyright', 'MIT License'),
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])],
)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='DecapAdjustment',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(root / 'assets' / 'app.ico'), version=version_info)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='DecapAdjustment')
