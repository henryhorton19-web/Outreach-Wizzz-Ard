"""Editing an existing sourcing preset must not corrupt it.

Three defects made editing unusable while creating worked:

1. editingPresetId was set when the editor opened and never reset, so after editing
   preset A every later save issued PUT /api/sourcing_prompts/A, overwriting A even
   when the user was creating something new. On a fresh page load the variable is
   null, which is why creating appeared to work.
2. openPresetEditor read sourcingPrompts without ensuring it was loaded, so an
   existing preset could open as a blank form, and saving that form wiped it.
3. savePreset rebuilt the object from form fields only, and the PUT endpoint does a
   full replace with no merge, so every omitted field reset to its Pydantic default
   on each edit.
"""
import pathlib
import re

import pytest

SRC = pathlib.Path("ui/app.js").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    """The body of a top-level async function, up to the next top-level declaration."""
    m = re.search(r"\nasync function %s\s*\([^)]*\)\s*\{" % re.escape(name), SRC)
    assert m, f"{name} not found in ui/app.js"
    start = m.end()
    nxt = re.search(r"\n(?:async )?function \w+\s*\(", SRC[start:])
    return SRC[start:start + (nxt.start() if nxt else 4000)]


def test_save_resets_the_editing_id():
    body = _fn("savePreset")
    assert "editingPresetId = null" in body, \
        "savePreset never clears editingPresetId, so the next save targets the old preset"


def test_cancel_resets_the_editing_id():
    m = re.search(r"presetCancelBtn\"\)\.onclick = [^;]+;", SRC)
    assert m, "the cancel handler was not found"
    assert "editingPresetId = null" in m.group(0), \
        "cancelling leaves editingPresetId set, so the next save targets the old preset"


def test_the_editor_ensures_prompts_are_loaded():
    body = _fn("openPresetEditor")
    assert "loadSourcingPrompts" in body, \
        "openPresetEditor reads sourcingPrompts without ensuring it is populated"
    idx_load = body.index("loadSourcingPrompts")
    idx_find = body.index("sourcingPrompts.find")
    assert idx_load < idx_find, "the load must happen before the lookup"


def test_save_preserves_existing_metadata():
    body = _fn("savePreset")
    assert "...existing" in body, \
        "savePreset does not merge the existing preset, so omitted fields reset to defaults"


def test_the_merge_puts_form_values_last():
    """Object spread applies left to right, so the form must come after existing or the
    user's edits are silently discarded. Object.assign(pdef, existing, pdef) has this
    backwards and was rejected for that reason."""
    body = _fn("savePreset")
    m = re.search(r"\{\s*\.\.\.existing\s*,\s*\.\.\.\w+\s*\}", body)
    assert m, "expected a merge of the shape { ...existing, ...pdef }"


def test_no_backwards_object_assign_merge():
    body = _fn("savePreset")
    assert not re.search(r"Object\.assign\(\s*pdef\s*,\s*existing", body), \
        "Object.assign(pdef, existing, ...) overwrites the user's edits with the old values"
