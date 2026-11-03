from pathlib import Path


def test_installer_paths_and_safe_upgrade_contract():
    root = Path(__file__).resolve().parents[1]
    text = (root / "packaging/installer.iss").read_text(encoding="utf-8")
    for directive in ("{autopf}", "PrivilegesRequiredOverridesAllowed=dialog",
                      "UsePreviousPrivileges=yes", "DisableDirPage=no"):
        assert directive in text
    assert "Check: not IsAdmin" in text or "[Run]" not in text
    script = (root / "scripts/verify_install_paths.ps1").read_text(encoding="utf-8")
    assert "previousTag" in script and "legacy-marker.txt" in script
    assert "/ALLUSERS" in script and "/CURRENTUSER" in script
