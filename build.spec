# PyInstaller spec — build a single desktop bundle.
#   Windows:  pyinstaller build.spec      -> dist/OutreachWizzard.exe
#   macOS  :  pyinstaller build.spec      -> dist/OutreachWizzard.app (add a .icns for a real icon)
#
# Bundles the UI (ui/), the engine files + briefs + voice shells (engine/), the shipped seed
# voices (app/seed_voices/), and forces in provider/runtime imports PyInstaller's static analysis
# can miss.

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

datas = [
    ("ui/*", "ui"),
    # engine (source + schema + briefs + the three voice shells), all verbatim
    ("engine/draft_engine.py", "engine"),
    ("engine/config.py", "engine"),
    ("engine/schema.json", "engine"),
    ("engine/composition_brief.md", "engine"),
    ("engine/enrichment_brief.md", "engine"),
    ("engine/voice_no_role_small.md", "engine"),
    ("engine/voice_role_small.md", "engine"),
    ("engine/voice_role_large.md", "engine"),
    # shipped seed voices (copied into the user data dir on first run)
    ("app/seed_voices/*.json", "app/seed_voices"),
]

# keyring backends + provider SDKs are imported dynamically; collect them explicitly.
hiddenimports = (
    collect_submodules("keyring")
    + collect_submodules("uvicorn")
    + ["google.genai", "anthropic", "openpyxl", "jsonschema", "multipart",
       "app.server", "app.providers.gemini", "app.providers.anthropic_provider",
       "app.providers.stub",
       "app.exemplars", "app.edit_align", "app.template_induct",
       "app.exemplar_voice", "app.exemplar_guards", "app.exemplar_replay"]
)
try:
    datas += collect_data_files("certifi")
except Exception:
    pass

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pandas", "matplotlib", "PyQt5", "PySide6", "tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="OutreachWizzard",
    icon=None,               # drop a ui/favicon.ico (win) or .icns (mac) here for a custom icon
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # no console window for the GUI app
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
