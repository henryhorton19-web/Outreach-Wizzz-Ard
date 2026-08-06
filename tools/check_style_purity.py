"""Style purity checker.

Enforces:
1. CSS: No hex literals (#xxx, #xxxxxx), rgb(), rgba(), hsl() outside :root {...}
   except for explicitly allowlisted rules (light-on-dark topbar/modal, shimmer, badges).
2. HTML: No inline style="..." containing color, background, border, font, or hex literals.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS_FILE = ROOT / "ui" / "styles.css"
HTML_FILE = ROOT / "ui" / "index.html"

CSS_ALLOWLIST = {
    # Topbar runs on --ground (dark); no light-on-dark tokens exist
    ".brand .sub",
    ".brand-title",
    ".topbar .signed",
    ".topbar .signed .role",
    ".topbar .pill-provider",
    ".topbar .icon-btn",
    ".topbar .icon-btn:hover",
    ".topbar-tab",
    ".topbar-tab:hover",
    ".topbar-tab.is-active",
    ".topbar-tab:focus-visible",
    ".cost-meter",
    ".cost-meter:hover",
    ".icon-btn.is-active",
    # Overlay containers & footer
    ".footer",
    ".modal-scrim",
    ".modal",
    ".snippet-popover",
    # Modal hero also on --ground
    ".modal-hero",
    ".modal-hero .eyebrow",
    ".modal-hero h2",
    ".modal-hero p.lede",
    ".modal-hero .logo",
    # Primary button — #fff on gradient is intentional
    ".btn.primary",
    "select.btn.primary",
    "select.btn.primary option",
    ".btn.danger",
    ".btn.danger:hover",
    # Badge count on caution bg
    ".badge-count",
    # Shimmer animation — decorative
    ".shimmer",
    # Success/OK badges — no green token
    ".badge-ok",
    ".pill-ok",
    ".pill-err",
    # Error variant borders
    ".badge-warn",
    ".research-fail",
    ".note-hard",
    ".note-soft",
    # Staleness dots — semantic green/amber/red
    ".stale-fresh",
    # Voice editor token chips — semantic purple/amber
    ".token-chip.experience",
    ".token-chip.experience:hover",
    ".token-chip.relevant",
    ".token-chip.relevant:hover",
    # Letter styling — near-white warmth on paper
    ".letter",
    ".body-edit",
    ".body-edit:focus",
    ".letter-body .emailEdit:focus",
    # Toast on dark bg
    ".toast",
}

FORBIDDEN_HTML_STYLE_PATTERNS = [
    re.compile(r"\bcolor\s*:", re.I),
    re.compile(r"\bbackground\s*:", re.I),
    re.compile(r"\bborder(-color)?\s*:\s*#[0-9a-fA-F]", re.I),
    re.compile(r"\bfont(-family|-size)?\s*:", re.I),
    re.compile(r"#[0-9a-fA-F]{3,6}\b"),
]


def check_css() -> list[str]:
    violations = []
    lines = CSS_FILE.read_text(encoding="utf-8").splitlines()
    in_root = False
    current_selector = ""
    color_pattern = re.compile(r"(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]+\)|hsla?\([^)]+\))")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if ":root" in stripped and "{" in stripped:
            in_root = True
            continue
        if in_root:
            if "}" in stripped:
                in_root = False
            continue

        if "{" in stripped and not stripped.startswith("/*"):
            current_selector = stripped.split("{")[0].strip()

        matches = color_pattern.findall(stripped)
        if matches:
            # Check allowlist against selector or selector parts
            sel_matched = any(
                allowed == current_selector or allowed in current_selector
                for allowed in CSS_ALLOWLIST
            )
            if not sel_matched:
                violations.append(f"ui/styles.css:{i}: [{current_selector}] hardcoded color '{matches[0]}'")

    return violations


def check_html() -> list[str]:
    violations = []
    lines = HTML_FILE.read_text(encoding="utf-8").splitlines()
    style_pattern = re.compile(r'style=["\']([^"\']+)["\']')

    for i, line in enumerate(lines, 1):
        for match in style_pattern.finditer(line):
            style_content = match.group(1)
            for pat in FORBIDDEN_HTML_STYLE_PATTERNS:
                if pat.search(style_content):
                    violations.append(f"ui/index.html:{i}: forbidden inline style property in '{style_content}'")
                    break

    return violations


def main() -> int:
    css_v = check_css()
    html_v = check_html()
    all_v = css_v + html_v
    for v in all_v:
        print(f"[FAIL] {v}")

    if all_v:
        print(f"=== FAIL ({len(all_v)} violations) ===")
        return 1
    print("=== PASS (0 violations) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
