# Third-party notices

The Windows distribution includes CPython (Python Software Foundation License),
Tcl/Tk (Tcl/Tk license), openpyxl and et_xmlfile (MIT and included notices), and
Pillow (HPND). Full license texts are distributed in `_internal/licenses/`.

The executable is built with PyInstaller, whose GPL license includes an exception
allowing distribution of frozen applications under their own license. Its full
license and exception are included in `_internal/licenses/pyinstaller/`.
Inno Setup produces the installer; its license permits distribution of installers.

Build dependencies are pinned in `requirements-build.txt`. Their presence in the
build environment does not imply that every build dependency is bundled at runtime.

References: [Python](https://docs.python.org/3/license.html),
[PyInstaller](https://pyinstaller.org/en/stable/license.html),
[Inno Setup](https://jrsoftware.org/files/is/license.txt).
