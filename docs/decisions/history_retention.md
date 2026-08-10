# Decision Record: Commit History Retention Strategy

**Date:** 2026-08-10  
**Status:** Approved (Option A)  
**Context:** During Stage 1 of repository restructuring (Execution Plan 15), employer-specific marketing positioning copy and colleague voice presets were removed from `HEAD`. Task 4 requires evaluating whether to rewrite historical git commits via `git filter-repo` or maintain history integrity.

## Decision: Option A — Maintain History & Clean HEAD

1. **Clean Current State (`HEAD`):** All employer-specific positioning and named colleague seed voices have been completely removed from `HEAD` and replaced with generic example data (`example_organisation`). `tools/check_vocabulary.py` and vocabulary gates confirm zero employer references in `HEAD`.
2. **Git History Integrity:** Option A preserves published commit history to prevent breaking existing clones or introducing visible force-push discontinuities in a candidate portfolio repository.
3. **Public Exposure Assessment:** The historical text consisted of public marketing positioning copy rather than authentication credentials, private keys, or personal API tokens.

**Conclusion:** Maintain history intact and keep `HEAD` clean.
