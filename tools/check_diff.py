"""Check git diff against statutory rules (no hardcoded hex/rgb, no new inline styles)."""
import subprocess
import re

def check_diff():
    res1 = subprocess.run(["git", "diff", "-U0", "--", "ui/"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    added_lines = [l for l in res1.stdout.splitlines() if l.startswith("+") and not l.startswith("+++")]

    hex_rgb_added = [l for l in added_lines if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", l)]
    print("=== Hardcoded Colours Added in ui/ ===")
    print(f"Count: {len(hex_rgb_added)}")
    for l in hex_rgb_added:
        print(l)

    res2 = subprocess.run(["git", "diff", "-U0", "--", "ui/index.html"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    added_html = [l for l in res2.stdout.splitlines() if l.startswith("+") and not l.startswith("+++")]
    styles_added = [l for l in added_html if 'style="' in l]
    print("\n=== New Inline style= Attributes Added in ui/index.html ===")
    print(f"Count: {len(styles_added)}")
    for l in styles_added:
        print(l)

if __name__ == "__main__":
    check_diff()
