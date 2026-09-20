import os

def is_mod_folder(path):
    """判断目录是否符合 RBVP Mod 判定规则。"""
    try:
        names = {name.casefold() for name in os.listdir(path)}
    except OSError:
        return False
    return (
        "addoninfo.txt" in names
        or "materials" in names
        or "models" in names
        or "scripts" in names
        or "particles" in names
        or "sound" in names
    )

def collect_mod_folders(parent_dir):
    folders=[]
    for name in os.listdir(parent_dir):
        path=os.path.join(parent_dir,name)
        if os.path.isdir(path) and name.casefold()!="temp" and not name.startswith(".") and is_mod_folder(path):
            folders.append(path)
    folders.sort(key=lambda p: os.path.basename(os.path.normpath(p)).casefold())
    return folders
