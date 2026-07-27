"""Company-name slugging.

Copied verbatim (behaviourally) from run_batch._slug so the app never imports run_batch,
which would drag pandas and the whole CLI onto the request hot path for a three-line function.
Kept byte-identical in behaviour so row keys match anything the CLI produced for the same name.
"""
from __future__ import annotations


def slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in (name or "x")).strip("_") or "x"
