import zipfile
import os

src_dir = os.path.dirname(os.path.abspath(__file__))
out_zip = os.path.join(os.path.dirname(os.path.dirname(src_dir)), "paris_app_outcome_aware.zip")

def should_exclude(path):
    parts = path.split(os.sep)
    return any(p in ('.venv', '__pycache__', '.git', '.DS_Store', 'node_modules') for p in parts)

with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
    for root, dirs, files in os.walk(src_dir):
        if should_exclude(root):
            continue
        for file in files:
            file_path = os.path.join(root, file)
            if should_exclude(file_path):
                continue
            arcname = os.path.join("paris", os.path.relpath(file_path, src_dir))
            zipf.write(file_path, arcname)

print("Zip created successfully at", out_zip)
