import os

def scan_mod_vmt_files(target_dir):
    materials_path=os.path.join(target_dir,"materials")
    scan_root=materials_path if os.path.isdir(materials_path) else target_dir
    files=[]; seen=set()
    for root_dir, dirs, file_names in os.walk(scan_root):
        dirs[:]=[d for d in dirs if d.casefold()!="vgui"]
        for file_name in file_names:
            if file_name.casefold().endswith(".vmt"):
                path=os.path.abspath(os.path.join(root_dir,file_name)); key=path.casefold()
                if key not in seen:
                    seen.add(key); files.append(path)
    return files

def modify_vmt_file(vmt_file, custom_text):
    try:
        with open(vmt_file,"r",encoding="utf-8",errors="ignore",newline="") as f: content=f.read()
        last_brace=content.rfind("}")
        if last_brace==-1: return False,"未找到结束大括号"
        newline="\r\n" if "\r\n" in content else "\n"
        prefix=content[:last_brace].rstrip("\r\n")
        suffix=content[last_brace:]
        new_content=prefix+newline+"\t"+custom_text.strip()+newline+suffix
        with open(vmt_file,"w",encoding="utf-8",newline="") as f: f.write(new_content)
        return True,""
    except Exception as e:
        return False,str(e)
