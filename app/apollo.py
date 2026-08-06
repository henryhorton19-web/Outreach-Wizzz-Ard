"""Apollo verification and cross-platform mail-draft opening.

Uses Apollo to verify email addresses, and stages each draft as an X-Unsent .eml that the OS default
mail handler opens in compose mode — `os.startfile` on Windows, `open` on macOS, `xdg-open` on Linux
— so it works the same on any machine (no Outlook/COM dependency)."""
from __future__ import annotations

import html as html_lib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate, make_msgid
from pathlib import Path

from . import settings as S
from . import attachments as attach_mod
from .models import CompanyState

APOLLO_URL = "https://api.apollo.io/api/v1/people/bulk_match"
BATCH_SIZE = 10
SLEEP_SECONDS = 1.0

# Bulk People Enrichment does NOT return emails unless you ask it to. `reveal_personal_emails`
# is a *query* parameter (not a body field); without it Apollo replies 200 but every match comes
# back with no `email`, so verification silently does nothing. Default on (that is the whole point
# of the verify step); it may consume Apollo credits, so it can be disabled via env.
# NB: reveal_phone_number is intentionally NOT set — Apollo makes it mandatory to also supply a
# webhook_url when phones are requested, which this desktop app has no endpoint for.
REVEAL_PERSONAL_EMAILS = (os.environ.get("WIZZARD_APOLLO_REVEAL_EMAILS") or os.environ.get("PARIS_APOLLO_REVEAL_EMAILS", "1")).strip().lower() not in {"0", "false", "no", ""}

KEEP_STATUSES = {"verified"}
HONORIFICS = {"dr", "mr", "ms", "mrs", "prof"}

_TLD_PAIRS = (
    ("nl", "be"), ("be", "nl"), ("us", "com"), ("de", "com"), 
    ("fr", "com"), ("es", "com"), ("ie", "com"), ("se", "com"), 
    ("dk", "com"), ("fi", "com"), ("no", "com"), ("pl", "com"),
)
_GENERIC_TLDS = frozenset({"com", "io", "net", "org", "co"})


def _domain_variants(domain: str) -> list[str]:
    if not domain or "." not in domain:
        return []
    sld, tld = domain.rsplit(".", 1)
    seen: set[str] = {domain}
    out: list[str] = []
    for src, dst in _TLD_PAIRS:
        if tld == src:
            candidate = f"{sld}.{dst}"
            if candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    if tld not in _GENERIC_TLDS:
        candidate = f"{sld}.com"
        if candidate not in seen:
            out.append(candidate)
    return out


class ApolloError(RuntimeError):
    """Raised when an Apollo API call fails, carrying the HTTP status and Apollo's message."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _post_json(url: str, payload: dict, api_key: str, query: dict | None = None) -> dict:
    if query:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(query)
    if not (api_key or "").strip():
        raise ApolloError("no Apollo API key configured", status=401)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Cache-Control", "no-cache")
    req.add_header("Accept", "application/json")
    req.add_header("x-api-key", api_key)  # Apollo requires the key in this header (not a param)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Apollo returns a JSON/text body explaining *why*; surface it instead of "HTTP Error 400".
        detail = ""
        try:
            raw = e.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw)
                detail = body.get("error") or body.get("message") or body.get("error_message") or raw
            except json.JSONDecodeError:
                detail = raw
        except Exception:
            pass
        detail = (detail or "").strip()[:300]
        hint = {
            401: "check the Apollo API key in Settings",
            403: "the Apollo key lacks access to this endpoint (a master key may be required)",
            422: "Apollo rejected the request parameters",
            429: "Apollo rate limit hit — try again shortly",
        }.get(e.code, "")
        msg = f"Apollo API {e.code}" + (f": {detail}" if detail else "") + (f" ({hint})" if hint else "")
        raise ApolloError(msg, status=e.code) from e
    except urllib.error.URLError as e:
        raise ApolloError(f"could not reach Apollo ({e.reason})") from e
    except (json.JSONDecodeError, ValueError) as e:
        raise ApolloError(f"Apollo returned an unreadable response ({e})") from e


def _names(full: str) -> tuple[str, str]:
    parts = (full or "").strip().split()
    while parts and parts[0].lower().rstrip(".") in HONORIFICS:
        parts = parts[1:]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def apollo_bulk_match(rows: list[dict], api_key: str) -> list[dict]:
    details = []
    valid_indices = []
    for i, r in enumerate(rows):
        first, last = _names(r.get("person", ""))
        d: dict[str, str] = {}
        if r.get("email"): d["email"] = r["email"]
        if first: d["first_name"] = first
        if last: d["last_name"] = last
        if r.get("domain"): d["domain"] = r["domain"].removeprefix("www.")
        
        # Apollo 400s on empty dicts. We need at least an email, domain, or name.
        if d.get("email") or d.get("domain") or (d.get("first_name") and d.get("last_name")):
            details.append(d)
            valid_indices.append(i)
    
    out: list[dict] = [{} for _ in range(len(rows))]
    if not details:
        return out

    query = {"reveal_personal_emails": "true"} if REVEAL_PERSONAL_EMAILS else None
    resp = _post_json(APOLLO_URL, {"details": details}, api_key, query=query)
    matches = resp.get("matches")
    if matches is None:
        matches = resp.get("people") or []
        
    for idx, match_idx in enumerate(valid_indices):
        m = matches[idx] if idx < len(matches) else None
        out[match_idx] = m if m else {}
        
    return out


def decide(meta_email: str, match: dict) -> dict:
    match = match or {}
    apollo_email = (match.get("email") or "").strip()
    status = (match.get("email_status") or "").strip().lower()
    if not match: decision = "no_match"
    elif status in KEEP_STATUSES: decision = "verified"
    elif not apollo_email: decision = "no_email"
    else: decision = "review"
    disc = bool(apollo_email and meta_email and apollo_email.lower() != meta_email.lower())
    return {
        "apollo_email": apollo_email,
        "apollo_email_status": status or "unknown",
        "decision": decision,
        "email_discrepancy": "yes" if disc else "no",
    }


# ---- Outlook Automation ----

_RESEARCH_SENTINEL = "\nRESEARCH DETAIL"

def _strip_research(body: str) -> str:
    idx = body.find(_RESEARCH_SENTINEL)
    return body[:idx].strip() if idx != -1 else body.strip()

def _to_html(text: str) -> str:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    parts = ["<html><body style='font-family:Calibri,Arial,sans-serif;font-size:11pt;'>"]
    for p in paragraphs:
        escaped = html_lib.escape(p).replace("\n", "<br>")
        parts.append(f"<p>{escaped}</p>")
    parts.append("</body></html>")
    return "\n".join(parts)

class OutlookError(RuntimeError):
    """Raised when the email draft could not be opened. Carries the already-written .eml path and
    its Message-ID so the caller can still record the send identity (the .eml exists on disk even
    when the OS mail handler fails to launch — e.g. a headless box or no default mail app)."""

    def __init__(self, message: str, message_id: str = "", path: str = ""):
        super().__init__(message)
        self.message_id = message_id
        self.path = path


def _build_eml(to: str, subject: str, body_text: str,
               attachments: list[Path] | None = None,
               message_id: str | None = None) -> tuple[bytes, str]:
    """Build an .eml that new Outlook opens in COMPOSE (editable + sendable) mode.

    Returns (bytes, message_id). The Message-ID is the reply/bounce match key: it was previously
    generated and discarded here; now it is captured so the SentItem can store it for the inbox
    sweep. A caller may pass a pre-chosen `message_id` (used when re-targeting a bounced send keeps
    the thread identity); otherwise one is minted.
    """
    plain = _strip_research(body_text)
    mid = (message_id or "").strip() or make_msgid()
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = mid
    msg["X-Unsent"] = "1"              # open as an editable draft, not a read-only message
    msg.set_content(plain)                                # text/plain fallback part
    msg.add_alternative(_to_html(plain), subtype="html")  # rich HTML part
    for path in (attachments or []):
        try:
            data = path.read_bytes()
        except OSError:
            continue  # a missing file must never fail the draft
        maintype, subtype = attach_mod.guess_mime(path)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
    return msg.as_bytes(policy=SMTP), mid


def _eml_dir():
    """Where staged .eml drafts are written before the OS mail app opens them.

    Cross-platform and portable: the default is OUTBOX_DIR under the per-user data dir
    (default: <data dir>/outbox; override with the eml_dir setting). A user can
    override it (Settings > eml_dir, or WIZZARD_EML_DIR) to route drafts to a findable/synced folder.
    Defensive: an unwritable override falls back to OUTBOX_DIR, then to a temp dir, so staging a
    draft never fails just because a configured path is missing on this machine."""
    candidates = []
    try:
        override = (S.load_settings().eml_dir or "").strip()
    except Exception:
        override = ""
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(S.OUTBOX_DIR)
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except OSError:
            continue
    # last resort: a temp dir (keeps the app working on a locked-down machine)
    import tempfile
    d = Path(tempfile.gettempdir()) / "OutreachWizzard"
    d.mkdir(parents=True, exist_ok=True)
    return d


def open_email_draft(to: str, subject: str, body_text: str,
                      attachments: list[Path] | None = None,
                      message_id: str | None = None,
                      company_name: str | None = None) -> tuple[str, str]:
    """Write the draft as an X-Unsent .eml and open it with the OS default mail handler.
    Returns (eml_path, message_id) so the caller can persist the Message-ID on the SentItem."""
    if not (to and "@" in to):
        raise OutlookError("no recipient email address")

    eml, mid = _build_eml(to, subject, body_text, attachments=attachments, message_id=message_id)
    if company_name:
        clean_name = "".join(ch for ch in str(company_name) if ch.isalnum() or ch in " -_").strip().lower() or "draft"
    else:
        clean_name = "".join(ch for ch in to.split("@")[0] if ch.isalnum())[:40] or "draft"
    path = _eml_dir() / f"{clean_name}.eml"
    path.write_bytes(eml)

    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 -- opens with the registered .eml handler
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["xdg-open", str(path)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError as e:
        raise OutlookError(f"could not launch the mail app to open {path.name}",
                           message_id=mid, path=str(path)) from e
    except subprocess.CalledProcessError as e:
        raise OutlookError(f"the OS could not open {path.name} ({e})",
                           message_id=mid, path=str(path)) from e
    except OSError as e:
        raise OutlookError(f"no mail app is associated with .eml files ({e})",
                           message_id=mid, path=str(path)) from e
    return str(path), mid


# ---- address ladder (Phase 0) ----------------------------------------------

def _pattern_permutations(first: str, last: str, domains: list[str]) -> list[str]:
    """Common corporate address patterns for a name across candidate domains, most-likely first.
    Deterministic and lowercase; used only as the bounce-retry fallback rungs, never to send blind.
    """
    first = (first or "").strip().lower()
    last = (last or "").strip().lower()
    out: list[str] = []
    for dom in domains:
        dom = (dom or "").strip().lower().removeprefix("www.")
        if not dom or "." not in dom:
            continue
        locals_: list[str] = []
        if first and last:
            locals_ += [f"{first}.{last}", f"{first}{last}", f"{first[0]}{last}",
                        f"{first}", f"{first}_{last}"]
        elif first:
            locals_ += [first]
        for lp in locals_:
            addr = f"{lp}@{dom}"
            if addr not in out:
                out.append(addr)
    return out


def rank_address_candidates(cache: dict | None, apollo_match: dict | None = None) -> list[dict]:
    """Return a ranked, deduped ladder of address candidates with provenance:
        [PRIMARY person] Apollo-verified > research contact.email > pattern permutations
        [ALT people]     each backup contact's email > their pattern permutations
    Each rung is {email, source, confidence, person_name, person_title, tier}. This is the substrate
    the bounce re-draft (Phase 6b) walks: on a bounce, drop the dead rung and take the next — the
    primary person's remaining formats first, then a DIFFERENT PERSON (`alt_person`). Pure +
    deterministic; no network. With no `contacts_alt` the output is byte-identical to before
    (off = today) aside from the always-present person fields, which default to the primary.
    """
    cache = cache or {}
    contact = (cache.get("contact") or {}) if isinstance(cache, dict) else {}
    company = (cache.get("company") or {}) if isinstance(cache, dict) else {}
    primary_name = (contact.get("name") or "")
    primary_title = (contact.get("title") or "")

    out: list[dict] = []
    seen: set[str] = set()

    def _add(email: str, source: str, confidence: str,
             person_name: str = "", person_title: str = "", tier: str = "primary_person"):
        e = (email or "").strip().lower()
        if not e or "@" not in e or e in seen:
            return
        seen.add(e)
        out.append({"email": e, "source": source, "confidence": confidence,
                    "person_name": person_name, "person_title": person_title, "tier": tier})

    am = apollo_match or {}
    a_email = (am.get("email") or "").strip()
    a_status = (am.get("email_status") or "").strip().lower()
    r_email = (contact.get("email") or "").strip()
    r_conf = (contact.get("email_confidence") or "").strip().lower()

    # base domain(s) shared by primary + alt pattern permutations
    base_domain = ""
    for src in (r_email, a_email):
        if src and "@" in src:
            base_domain = src.split("@", 1)[1]
            break
    if not base_domain:
        w = (company.get("website") or company.get("domain") or "").strip()
        w = w.replace("https://", "").replace("http://", "").split("/")[0]
        base_domain = w
    domains = []
    if base_domain:
        domains = [base_domain.removeprefix("www.")] + _domain_variants(base_domain.removeprefix("www."))

    alts = [a for a in (cache.get("contacts_alt") or []) if isinstance(a, dict)][:2] \
        if isinstance(cache, dict) else []

    # PASS 1 — KNOWN addresses, by person, primary first. A *named different person's* known
    # address is a better bounce-retry than low-confidence pattern-guesses of the primary, so all
    # known addresses (primary + alts) precede any pattern permutations. This is what makes the
    # escalation to a "different person and format" actually reachable within the retry budget.
    if a_email:
        _add(a_email, "apollo", "high" if a_status in KEEP_STATUSES else "medium",
             person_name=primary_name, person_title=primary_title, tier="primary_person")
    if r_email:
        conf = "high" if r_conf in {"high", "verified"} else ("medium" if r_conf in {"medium", "pattern"} else "low")
        _add(r_email, "research", conf,
             person_name=primary_name, person_title=primary_title, tier="primary_person")
    for alt in alts:
        alt_name = (alt.get("name") or "").strip()
        if not alt_name or alt_name.lower() == (primary_name or "").strip().lower():
            continue
        alt_email = (alt.get("email") or "").strip()
        if alt_email:
            ac = (alt.get("email_confidence") or "").strip().lower()
            aconf = "high" if ac in {"high", "verified"} else ("medium" if ac in {"medium", "pattern"} else "low")
            _add(alt_email, "research", aconf,
                 person_name=alt_name, person_title=(alt.get("title") or ""), tier="alt_person")

    # PASS 2 — pattern permutations (all low confidence): primary person first, then alt people.
    first, last = _names(primary_name)
    for addr in _pattern_permutations(first, last, domains):
        _add(addr, "pattern", "low",
             person_name=primary_name, person_title=primary_title, tier="primary_person")
    for alt in alts:
        alt_name = (alt.get("name") or "").strip()
        if not alt_name or alt_name.lower() == (primary_name or "").strip().lower():
            continue
        alt_email = (alt.get("email") or "").strip()
        af, al = _names(alt_name)
        alt_domains = domains
        if alt_email and "@" in alt_email:
            ad = alt_email.split("@", 1)[1].removeprefix("www.")
            alt_domains = [ad] + [d for d in domains if d != ad]
        for addr in _pattern_permutations(af, al, alt_domains):
            _add(addr, "pattern", "low",
                 person_name=alt_name, person_title=(alt.get("title") or ""), tier="alt_person")

    return out


# ---- Main Entry Point ----

def apollo_verify(rows: list[CompanyState], voice: str, api_key: str | None = None) -> dict:
    if not rows:
        return {"provider": "apollo", "status": "processed", "count": 0, "opened": 0}

    st = S.load_settings()
    requested_default = st.default_attachments if st.attach_by_default else []
    default_paths = attach_mod.resolve_paths(requested_default)
    missing_default = len(requested_default) - len(default_paths)

    # Extract needed fields
    extracted = []
    for cs in rows:
        cache = cs.cache or {}
        contact = cache.get("contact") or {}
        spec = cs.spec or {}
        
        email = spec.get("send_to") or contact.get("email") or ""
        person = contact.get("name") or ""
        domain = ""
        if email and "@" in email:
            domain = email.split("@")[1]
        elif cs.website:
            domain = cs.website.replace("https://", "").replace("http://", "").split("/")[0]

        extracted.append({
            "cs": cs,
            "email": email,
            "person": person,
            "domain": domain
        })

    api_error = ""  # first Apollo API failure, if any — surfaced in the receipt note
    if api_key:
        pending = []
        for start in range(0, len(extracted), BATCH_SIZE):
            chunk = extracted[start:start + BATCH_SIZE]
            api_rows = [{"email": r["email"], "person": r["person"], "domain": r["domain"]} for r in chunk]
            try:
                matches = apollo_bulk_match(api_rows, api_key)
            except ApolloError as e:
                if not api_error:
                    api_error = str(e)
                print(f"Apollo API error: {e}", file=sys.stderr)
                matches = [{} for _ in range(len(chunk))]
            except Exception as e:  # never let enrichment failure block staging the draft
                if not api_error:
                    api_error = f"unexpected Apollo error: {e}"
                print(f"Apollo API error: {e}", file=sys.stderr)
                matches = [{} for _ in range(len(chunk))]

            for r, m in zip(chunk, matches):
                pending.append((r, m))

            if start + BATCH_SIZE < len(extracted):
                time.sleep(SLEEP_SECONDS)

        # Fallback domains pass (skip entirely once the API is known-bad, e.g. auth failure)
        for i, (r, m) in enumerate(pending):
            if m or api_error:
                continue
            primary = r["domain"].removeprefix("www.")
            auto = _domain_variants(primary)
            for fallback in auto:
                try:
                    retry = apollo_bulk_match([{"email": r["email"], "person": r["person"], "domain": fallback}], api_key)
                except ApolloError as e:
                    if not api_error:
                        api_error = str(e)
                    break
                except Exception:
                    break
                time.sleep(SLEEP_SECONDS)
                if retry and retry[0]:
                    pending[i] = (r, retry[0])
                    break
    else:
        pending = [(r, {}) for r in extracted]

    opened = 0
    results = []
    for r, m in pending:
        cs = r["cs"]
        decision = decide(r["email"], m)

        # Determine the best email to send to
        send_to = decision["apollo_email"] or r["email"]
        if send_to:
            if cs.spec is None:
                cs.spec = {}
            cs.spec["send_to"] = send_to

        row = {"name": cs.name, "email": send_to, "status": "", "error": "", "message_id": ""}
        if not (send_to and "@" in send_to):
            row["status"] = "no_email"
            results.append(row)
            continue

        safe_subject = cs.subject or 'Draft'
        row_names = getattr(cs, "attachments", None) or []
        paths = attach_mod.resolve_paths(row_names) if row_names else default_paths
        try:
            _path, mid = open_email_draft(
                to=send_to,
                subject=safe_subject,
                body_text=cs.final_email or "",
                attachments=paths,
                company_name=cs.slug or cs.name,
            )
            opened += 1
            row["status"] = "opened"
            row["message_id"] = mid
        except OutlookError as e:
            # the .eml (with its Message-ID) is written even when the OS handler fails to launch;
            # keep the send identity so reply/bounce detection can still match this send.
            row["status"] = "failed"
            row["error"] = str(e)
            row["message_id"] = getattr(e, "message_id", "") or ""
            print(f"Failed to open email draft for {cs.name}: {e}", file=sys.stderr)
        except Exception as e:
            row["status"] = "failed"
            row["error"] = str(e)
            print(f"Failed to open email draft for {cs.name}: {e}", file=sys.stderr)
        results.append(row)

    failed = [x for x in results if x["status"] == "failed"]
    no_email = [x for x in results if x["status"] == "no_email"]
    if failed:
        note = "Could not open the email: " + failed[0]["error"]
    elif no_email and not opened:
        note = "No email address to open."
    elif opened:
        note = "Opened draft(s) in your email app."
        if missing_default:
            note += " (a configured attachment was missing and was skipped)"
    else:
        note = "No emails to open."
    if api_error:
        # Verification degraded — the drafts still staged, but the user should know why no
        # Apollo email/verification came back (this is what surfaced as "bad api call" before).
        note = f"Email verification skipped — {api_error}. " + note

    return {
        "provider": "apollo",
        "status": "processed",
        "count": len(rows),
        "opened": opened,
        "failed": len(failed),
        "no_email": len(no_email),
        "api_error": api_error,
        "results": results,
        "note": note,
    }
