# Engineering Decisions & Failure Analysis

## 1. Problem Statement and Architectural Constraints

Personalized outreach requires thorough per-target research to produce relevant communications. Performing manual research for every prospect is slow, while ungrounded language model generation risks fabricating company metrics, customer logos, or contact details. The primary technical constraint of Outreach Wizz-ard was designing a system capable of producing highly tailored outreach emails without either hallucinating target facts or incurring expensive, repetitive web research calls whenever a user edits a template or requests a redraft.

To solve this, the application enforces a strict boundary between web-grounded research retrieval and deterministic email drafting.

## 2. Key Architecture Decisions and Trade-offs

### Two-Stage Schema Contract
The system decouples web-grounded research from draft composition using a validated JSON contract (`engine/schema.json`). Research executes once per target company, capturing verified proof points, recent milestone triggers, and contact details into a structured cache. Email composition consumes this cached contract deterministically. 

*Trade-off:* Decoupling research from composition means redrafts and template tweaks complete instantly with zero additional model latency or API expense. The trade-off is cache staleness: if a company announces a new milestone after initial ingestion, the research cache must be manually invalidated to reflect the update.

### Hard Verification Floor Over Advisory Warnings
Rather than surfacing soft warnings when a model includes an unsourced statistic, Outreach Wizz-ard implements a hard verification floor (`engine/draft_engine.py`). Every numerical figure, customer reference, and milestone claim in a generated draft is cross-checked against allowed research facts and candidate profile data. Any draft containing an unsourced claim or domain discrepancy is rejected automatically.

*Trade-off:* A hard gate occasionally rejects acceptable drafts that express a fact using unusual phrasing. However, accepting this slight over-rejection rate guarantees that no fabricated figures or unsupported claims reach sent communications.

### Hybrid Retrieval Before LLM Reranking
Candidate experience selection uses a keyword-based recall stage prior to optional LLM reranking. The domain matcher ranks experiences by keyword overlap against target attributes, producing a constrained shortlist before invoking a model reranker.

*Trade-off:* Relying on keyword matching for recall prevents the model from selecting unlisted experiences. In exchange, if an LLM provider experiences an outage, the system gracefully degrades to keyword-matched retrieval rather than failing or halting the drafting pipeline.

## 3. Failure Analysis and Remediation

### Batch Drafting HTTP 500 Under High Test Pass Count
*Incident:* The batch drafting endpoint (`draft_all`) threw an HTTP 500 error on every invocation despite 285 unit tests passing cleanly.

*Root Cause:* The refactored batch handler instantiated `threading.Thread` to execute background drafts, but `import threading` had been omitted from the module header. Existing test suites thoroughly covered single-company drafting endpoints but lacked an integration test for the newly refactored batch route.

*Resolution & Lesson:* The fix required importing `threading` and adding comprehensive integration tests for batch execution routes. The engineering lesson was that high test counts do not equal full architectural coverage; newly added or refactored execution paths require explicit integration testing regardless of overall test suite size.

### Unpinned Contact Email Hallucination
*Incident:* Early research runs generated plausible contact email addresses that belonged to unrelated third-party domains.

*Root Cause:* The research prompt originally instructed the model to "provide a best-guess contact email (NEVER blank)" without constraining the generated domain. When a contact's email address was missing from public sources, the model synthesized plausible addresses using external domain suffixes.

*Resolution & Lesson:* The ingestion pipeline was updated to resolve and pin the target company's official web domain first. Contact discovery rules now strictly validate generated email addresses against the pinned domain, returning an empty contact field rather than an unverified or misattributed email address.

### Event Loop Blocking Across Async Route Handlers
*Incident:* Executing a batch drafting run froze all four application UI tabs, causing pywebview interface freezes.

*Root Cause:* Ninety-two route handlers in FastAPI were declared using `async def` despite performing synchronous CPU work and blocking file I/O. In FastAPI, declaring a synchronous function with `async def` forces it to execute directly on the main asyncio event loop thread rather than in an external thread pool, blocking all incoming HTTP requests for up to 2.64 seconds per operation.

*Resolution & Lesson:* Removing `async` from handlers that do not explicitly `await` asynchronous calls allowed FastAPI to delegate those routines to its background worker thread pool. This restored smooth UI responsiveness across all interface tabs during heavy drafting passes.

## 4. Known Open Limitations

* **Manual Cache Invalidation:** The research cache relies on explicit user invalidation rather than time-based automatic expiration.
* **Local Token Cost Estimation:** API usage cost is estimated locally based on token counts rather than fetched directly from live provider billing webhooks.
