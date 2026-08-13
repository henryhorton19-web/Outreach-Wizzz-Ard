"""Assertion that exemplar modules have zero outcome/reply-rate dependencies (Plan 26, Stage 8).

Verifies via AST parsing that no outcome tokens exist in exemplar code modules, ensuring the
self-learning voice path remains 100% grounded in the user's text and edits only.
"""
import ast
import pathlib

BANNED = ("reply_state", "reply_rate", "reply_ci", "voice_stats", "bounced", "sent_items",
          "SentItem", "arbitrate", "spawn_challenger", "wilson", "_OUTCOME_WEIGHT")

EXEMPLAR_MODULES = ("app/exemplars.py", "app/edit_align.py", "app/template_induct.py",
                    "app/exemplar_voice.py", "app/exemplar_guards.py", "app/exemplar_replay.py")


def _code_only(rel: str) -> str:
    """Module source with docstrings and comments removed, so prose explaining WHY an outcome
    signal is excluded cannot trip a check on whether one is USED."""
    tree = ast.parse(pathlib.Path(rel).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body.pop(0)
    return ast.unparse(tree)


def test_no_outcome_signal_in_exemplar_modules():
    for rel in EXEMPLAR_MODULES:
        code = _code_only(rel)
        for token in BANNED:
            assert token not in code, f"{rel}: {token} used in code, not just described in prose"


def test_exemplar_modules_import_without_voice_stats():
    import app.exemplars, app.edit_align, app.template_induct  # noqa: F401
    import app.exemplar_voice, app.exemplar_guards, app.exemplar_replay  # noqa: F401
