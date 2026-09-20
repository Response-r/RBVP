import os
import shutil

try:
    import winreg
except ImportError:
    winreg = None

def format_path(path: str) -> str:
    if not path:
        return ""
    norm = os.path.normpath(path.strip())
    if len(norm) >= 2 and norm[1] == ":":
        norm = norm[0].upper() + norm[1:]
    return norm


def find_steam_install_paths():
    steam_paths = []
    if winreg:
        for hive in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
            for subkey in [
                r"SOFTWARE\Valve\Steam",
                r"SOFTWARE\WOW6432Node\Valve\Steam",
            ]:
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        val, _ = winreg.QueryValueEx(key, "SteamPath")
                        if val and os.path.exists(val):
                            steam_paths.append(format_path(val))
                except Exception:
                    pass
    drives = ["C:", "D:", "E:", "F:", "G:", "H:"]
    for drive in drives:
        steam_paths.extend([
            f"{drive}\\Program Files (x86)\\Steam",
            f"{drive}\\Program Files\\Steam",
            f"{drive}\\Steam",
            f"{drive}\\SteamLibrary",
        ])
    return list(dict.fromkeys([p for p in steam_paths if os.path.exists(p)]))


def auto_find_vpk_exe():
    vpk_in_path = shutil.which("vpk") or shutil.which("vpk.exe")
    if vpk_in_path and os.path.exists(vpk_in_path):
        return format_path(vpk_in_path)

    steam_paths = find_steam_install_paths()
    game_sub_paths = [
        r"steamapps\common\Left 4 Dead 2\bin\vpk.exe",
        r"steamapps\common\Counter-Strike Global Offensive\bin\vpk.exe",
        r"steamapps\common\Team Fortress 2\bin\vpk.exe",
        r"steamapps\common\GarrysMod\bin\vpk.exe",
        r"steamapps\common\Portal 2\bin\vpk.exe",
    ]
    for base in steam_paths:
        for sub in game_sub_paths:
            full = os.path.join(base, sub)
            if os.path.exists(full):
                return format_path(full)
    return ""


def auto_find_vtfedit_exe():
    vtf_in_path = shutil.which("VTFEdit.exe") or shutil.which("vtfedit")
    if vtf_in_path and os.path.exists(vtf_in_path):
        return format_path(vtf_in_path)

    drives = ["C:", "D:", "E:", "F:", "G:"]
    candidates = []
    for d in drives:
        candidates.extend([
            f"{d}\\Program Files\\VTFEdit\\VTFEdit.exe",
            f"{d}\\Program Files (x86)\\VTFEdit\\VTFEdit.exe",
            f"{d}\\VTFEdit\\VTFEdit.exe",
            f"{d}\\tools\\VTFEdit\\VTFEdit.exe",
        ])
    for c in candidates:
        if os.path.exists(c):
            return format_path(c)
    return ""


def auto_find_addons_dir():
    steam_paths = find_steam_install_paths()
    game_addons = [
        r"steamapps\common\Left 4 Dead 2\left4dead2\addons",
        r"steamapps\common\Counter-Strike Global Offensive\csgo\addons",
    ]
    for base in steam_paths:
        for sub in game_addons:
            full = os.path.join(base, sub)
            if os.path.exists(full):
                return format_path(full)
    return ""

