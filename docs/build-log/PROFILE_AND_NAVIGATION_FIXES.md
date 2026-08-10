> Historical build note ? kept for provenance, not maintained.

# Candidate Profile & Header Navigation Diagnosis Record

## 1. Exception Ordering in Candidate Profile Modal
`openProfileModal()` initially placed `backdrop.classList.remove("hidden")` as the last statement inside its `try` block. Any uncaught throw earlier in the execution path—specifically the missing `#profStandingExp` input element in static markup—aborted execution before reaching the unhide call. Hoisting `showModal("profileModal")` to the top of the function ensures the modal appears immediately, while restoring `#profStandingExp` fixes the input binding. This was an exception-ordering defect, not a microtask or paint-timing issue.

## 2. Top-Bar Tab Navigation Symptom
The 5 view tabs and header controls were proven via DOM inspection to have `wire()` complete with all click handlers bound. The unresponsiveness of the 5 top-bar view tabs was not caused or resolved by code changes to `openProfileModal` (which only handles `#profileBtn`). The root cause of the temporary unresponsiveness was a stale cached `ui/app.js` or stale background server process, which was resolved upon hard-reloading the browser during probing.

## 3. Scope of Retained Fixes
All changes are retained because they repair genuine underlying defects:
- Restored missing `#profStandingExp` DOM input in `ui/index.html`.
- Aligned `#profileModal` backdrop and modal classes to design-system tokens (`.modal-scrim` and `.modal`).
- Corrected exception ordering in `openProfileModal()` and added server-side validation (`422` error on empty required fields/whitelists) plus client-side load guards to prevent blank profile overwrites.
