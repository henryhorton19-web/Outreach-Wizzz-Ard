"""Automated WCAG 2.2 contrast check for tokens in ui/styles.css."""
import sys
import re
from pathlib import Path

STYLES_FILE = Path(__file__).parent.parent / "ui" / "styles.css"

PAIRS = [
    ("ink",       "panel",   4.5),
    ("ink",       "paper",   4.5),
    ("ink",       "panel-2", 4.5),
    ("ink-soft",  "panel",   4.5),
    ("ink-soft",  "paper",   4.5),
    ("ink-faint", "panel",   4.5),
    ("ink-faint", "paper",   4.5),
    ("ink-soft",  "panel-2", 4.5),
    ("line-strong", "panel", 3.0),  # input borders, SC 1.4.11
    ("capital",   "panel",   3.0),  # focus ring
    ("caution",   "caution-tint", 4.5),
    ("error",     "error-tint", 4.5),
]


def parse_tokens(css_path: Path) -> dict[str, str]:
    text = css_path.read_text(encoding="utf-8")
    root_match = re.search(r":root\s*\{([^}]+)\}", text)
    if not root_match:
        raise ValueError("Could not find :root block in styles.css")

    root_content = root_match.group(1)
    tokens = {}
    for line in root_content.splitlines():
        line = line.strip()
        m = re.match(r"--([a-zA-Z0-9_-]+)\s*:\s*(#[0-9a-fA-F]{3,8})", line)
        if m:
            tokens[m.group(1)] = m.group(2)
    return tokens


def hex_to_rgb(hex_str: str) -> tuple[float, float, float]:
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join([c * 2 for c in hex_str])
    r = int(hex_str[0:2], 16) / 255.0
    g = int(hex_str[2:4], 16) / 255.0
    b = int(hex_str[4:6], 16) / 255.0
    return r, g, b


def rel_luminance(r: float, g: float, b: float) -> float:
    def channel_lum(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    rl = channel_lum(r)
    gl = channel_lum(g)
    bl = channel_lum(b)
    return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl


def contrast_ratio(hex1: str, hex2: str) -> float:
    l1 = rel_luminance(*hex_to_rgb(hex1))
    l2 = rel_luminance(*hex_to_rgb(hex2))
    lmax = max(l1, l2)
    lmin = min(l1, l2)
    return (lmax + 0.05) / (lmin + 0.05)


def check_contrast() -> bool:
    tokens = parse_tokens(STYLES_FILE)
    all_passed = True

    print("=== WCAG 2.2 Relative Luminance Token Contrast Check ===")
    for fg_name, bg_name, req_ratio in PAIRS:
        if fg_name not in tokens or bg_name not in tokens:
            print(f"FAIL: Missing token {fg_name} or {bg_name}")
            all_passed = False
            continue

        fg_hex = tokens[fg_name]
        bg_hex = tokens[bg_name]
        ratio = contrast_ratio(fg_hex, bg_hex)
        passed = ratio >= req_ratio
        status = "PASS" if passed else "FAIL"

        print(f"[{status}] --{fg_name:<12} ({fg_hex}) vs --{bg_name:<12} ({bg_hex}) => {ratio:.2f}:1 (Req: {req_ratio}:1)")
        if not passed:
            all_passed = False

    return all_passed


if __name__ == "__main__":
    success = check_contrast()
    sys.exit(0 if success else 1)
