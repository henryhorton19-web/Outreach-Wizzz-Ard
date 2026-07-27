"""App-level settings (NOT the engine's config.py, which is content-only).

Everything here is orchestration configuration: which model provider to use, the model IDs
(configurable and overridable, because provider model IDs drift and must be verifiable at run
time rather than trusted from a hardcoded string), search caps, concurrency, and paths.

Precedence for any setting: environment variable  >  settings.json in the user data dir  >
the documented default below. Model IDs in particular are meant to be changed in Settings.

Model choices below are carried over verbatim from the Example Capital app (same standing policy):
research on 2.5 Flash, compose on 2.5 Pro; Claude available as the optional provider.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, asdict, field
from pathlib import Path


# ---- paths -----------------------------------------------------------------

def _data_root() -> Path:
    """Per-user writable dir for batch JSON, caches, audit records, settings.
    Windows: %APPDATA%\\ParisOutreach ; else ~/.paris_outreach . Overridable via PARIS_DATA_DIR."""
    override = os.environ.get("PARIS_DATA_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "ParisOutreach"
    return Path.home() / ".paris_outreach"


DATA_DIR = _data_root()
CACHE_DIR = DATA_DIR / "caches"
BATCH_DIR = DATA_DIR / "batches"
AUDIT_DIR = DATA_DIR / "audit"
VOICES_DIR = DATA_DIR / "voices"
VOICE_HISTORY_DIR = DATA_DIR / "voice_history"   # per-voice snapshots for rollback (Layer 4)
ATTACH_DIR = DATA_DIR / "attachments"
OUTBOX_DIR = DATA_DIR / "outbox"      # staged .eml files; cross-platform default under the data dir
SETTINGS_FILE = DATA_DIR / "settings.json"

for _d in (DATA_DIR, CACHE_DIR, BATCH_DIR, AUDIT_DIR, VOICES_DIR, VOICE_HISTORY_DIR,
           ATTACH_DIR, OUTBOX_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def ensure_seeded() -> None:
    """Bootstrap-once PER KIND: if the store has ZERO voices of a given kind, insert that kind's
    shipped starter voices. Outreach voices seed from seed_voices/; follow-up voices seed from
    seed_followup_voices/. Each kind is independent, so a user who already has outreach voices still
    gets follow-up voices on upgrade, deleting one voice never re-adds it, and wiping every voice of
    a kind self-heals that kind on next launch. The user owns their edits once any of a kind exists.
    """
    import json as _json
    try:
        existing = []
        for p in VOICES_DIR.glob("*.json"):
            try:
                existing.append(_json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
    except Exception:
        existing = []

    def _have(kind: str) -> bool:
        return any((v.get("kind", "outreach") or "outreach") == kind for v in existing)

    import shutil
    pkg = Path(__file__).resolve().parent
    # seed_voices/ ships generic starter voices (committed). seed_voices_local/ is git-ignored
    # and holds your real/private voices; if present it seeds too, so a fresh clone with your
    # local dir present bootstraps your actual voices before the private data repo syncs them.
    plan = [
        ("outreach", pkg / "seed_voices"),
        ("outreach", pkg / "seed_voices_local"),
        ("followup", pkg / "seed_followup_voices"),
    ]
    for kind, seed_dir in plan:
        if _have(kind) or not seed_dir.exists():
            continue
        for src in seed_dir.glob("*.json"):
            try:
                shutil.copyfile(src, VOICES_DIR / src.name)
            except Exception:
                pass


ensure_seeded()


# ---- provider / model defaults (verbatim from HPE app) ---------------------
# Standing policy (2026-07): research on 2.5 Flash, compose on 2.5 Pro.
# IDs are DEFAULTS ONLY — verify current IDs against the provider docs and override in Settings:
#   Gemini : https://ai.google.dev/gemini-api/docs/models
#   Claude : https://docs.claude.com/en/docs/about-claude/models
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"       # research / base model
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4.6"

# Anthropic web-search tool version. web_search_20250305 is the broadly-supported baseline.
ANTHROPIC_WEB_TOOL = "web_search_20250305"

VALID_PROVIDERS = ("gemini", "anthropic", "stub")

# The three role-situation keys, derived from research signals (role_exists x company_size) in the
# pipeline. These are ROUTING CATEGORIES, not voice content: a voice becomes eligible for one by
# carrying it in its `situations` tag. Voice frame content lives entirely in the editable store.
VALID_VOICES = ("no_role_small", "role_small", "role_large")


@dataclass
class Settings:
    provider: str = "gemini"                     # gemini (default) | anthropic | stub
    default_voice: str = "chief_of_staff"        # fallback voice id when no voice is tagged for a situation
    last_session_voice: str = ""                 # session voice remembered across app restarts
    gemini_model: str = DEFAULT_GEMINI_MODEL     # research + base (2.5-flash)
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    # Standing policy: compose on 2.5 Pro (output-only max_output_tokens; thinking is separate).
    compose_model: str = "gemini-2.5-pro"
    research_temperature: float = 0.2            # research wants faithfulness, low temp
    compose_temperature: float = 0.7             # compose wants some voice/variety
    # Research thinking: 2.5-flash uses budget (not level). 0 disables thinking entirely.
    research_thinking_budget: int = 0
    research_thinking_level: str = "low"
    research_max_output_tokens: int = 12000
    # Compose thinking: 2.5 Pro ignores level; use budget. CANNOT be 0 on 2.5 Pro (min 128).
    compose_thinking_level: str = ""
    compose_thinking_budget: int = 128
    compose_max_output_tokens: int = 2048
    helper_thinking_level: str = "minimal"
    helper_model: str = "gemini-3.1-flash-lite"
    helper_max_output_tokens: int = 256
    fallback_model: str = "gemini-3.5-flash"     # kept as fallback; used when available
    max_web_searches: int = 4                    # per-target search cap (cost control)
    research_concurrency: int = 5                # bounded parallel research/compose workers
    request_timeout_s: int = 120
    max_retries: int = 2                          # 2 primary + 1 fallback
    host: str = "127.0.0.1"                       # NEVER bind to 0.0.0.0
    port: int = 8770
    default_attachments: list[str] = field(default_factory=list)  # managed filenames under ATTACH_DIR
    attach_by_default: bool = True
    # Where staged .eml drafts are written before the OS mail app opens them. Empty = the
    # cross-platform default (OUTBOX_DIR under the data dir). Set to an absolute path — e.g. a synced
    # Documents/OneDrive folder — to route them somewhere findable; per-machine, so it stays portable.
    eml_dir: str = ""

    # ---- automated follow-up (CRM-style) ----
    # On approving an outreach email, enrol a follow-up for the next step (if under the cap).
    # Delays chain from the ORIGINAL's approval time. Defaults encode the 2026 3-7-7 cadence
    # (Day 3, Day 10, Day 17). max_steps defaults to 1 (a single follow-up); raise to 2-3 for a
    # multi-touch cadence — approving a follow-up re-enrols the next step up to the cap.
    follow_up_enabled: bool = True
    follow_up_max_steps: int = 1
    follow_up_delay_days: list[int] = field(default_factory=lambda: [3, 7, 7])

    # ---- Phase 1e: cost meter ----
    # Per-model price table in USD per 1M tokens: {model_id: {"in": x, "out": y, "cached": z}}.
    # Used to price the token counts providers return. Empty entries price to 0 (never alarming).
    cost_prices: dict = field(default_factory=lambda: {
        "gemini-2.5-flash": {"in": 0.30, "out": 2.50, "cached": 0.075},
        "gemini-2.5-pro": {"in": 1.25, "out": 10.0, "cached": 0.31},
        "claude-sonnet-4.6": {"in": 3.0, "out": 15.0, "cached": 0.30},
    })

    # ---- Phase 2: pipeline board ----
    pipeline_stale_days: int = 7                  # a Sent card with no movement past this is "quiet"

    # ---- Phase 3: voice stats ----
    voice_stats_min_n: int = 15                   # below this, show "not enough data yet", never a %

    # ---- Phase 5: inbox (reply/bounce detection) — all opt-in, off = today ----
    imap_enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_ssl: bool = True
    imap_mailboxes: list[str] = field(default_factory=lambda: ["INBOX"])
    imap_poll_minutes: int = 0                    # 0 = manual sweep only
    imap_confirm_replies: bool = False            # ask before marking an ambiguous match replied

    # ---- Phase 6b: bounce re-draft ----
    # Retries walk the ranked ladder: the current person's remaining address formats first, then a
    # DIFFERENT PERSON (an alt contact). Default 3 so at least one alternate person is reached.
    max_bounce_retries: int = 3                   # <= ladder length; exhaustion -> bounced_exhausted

    # ---- Phase 6c: send-window advisory ----
    send_window_advisory: bool = True             # non-blocking "consider staging Monday AM" hint

    # ---- Phase 7: learning routing (bandit) — picks WHICH voice runs ----
    voice_learning_routing: str = "off"           # off | suggest | auto
    voice_explore_epsilon: float = 0.1            # exploration rate for new/edited voices

    # ---- Layer 4: continuous voice-CONTENT learning (updates HOW a voice writes) ----
    # Distils your (machine draft -> approved edit) diffs into structured voice updates so drafts
    # drift toward how you write. off = today (byte-for-byte). suggest = propose a patch you accept
    # in the Voices editor. auto = apply automatically, but VERSIONED (one-click rollback), bounded
    # per cycle, gated on min-edits + a cooldown. Off = today.
    voice_learning_mode: str = "auto"             # off | suggest | auto
    voice_learning_min_edits: int = 5             # new edits before a cycle may fire (2-5 suffice for style)
    voice_learning_max_examples: int = 5          # cap on learned gold examples per voice (rotation)
    voice_learning_cooldown_hours: int = 12       # min gap between auto cycles for one voice
    voice_learning_promote: bool = False          # Phase C: A/B a learned change as a challenger,
    #                                               arbitrated by the reply-rate bandit before it wins
    voice_learning_reflection_model: str = ""     # empty = use helper_model (cheap; reflection is small)

    def sanitized(self) -> dict:
        d = asdict(self)
        if d.get("provider") not in VALID_PROVIDERS:
            d["provider"] = "gemini"
        da = d.get("default_attachments") or []
        d["default_attachments"] = [n for n in da if isinstance(n, str) and (ATTACH_DIR / n).is_file()]
        d["attach_by_default"] = bool(d.get("attach_by_default", True))
        d["follow_up_enabled"] = bool(d.get("follow_up_enabled", True))
        try:
            d["follow_up_max_steps"] = max(0, min(3, int(d.get("follow_up_max_steps", 1))))
        except (TypeError, ValueError):
            d["follow_up_max_steps"] = 1
        dd = d.get("follow_up_delay_days") or [3, 7, 7]
        if not isinstance(dd, list) or not dd:
            dd = [3, 7, 7]
        d["follow_up_delay_days"] = [max(0, int(x)) for x in dd if isinstance(x, (int, float))] or [3, 7, 7]

        # ---- new-phase fields ----
        try:
            d["voice_stats_min_n"] = max(1, min(500, int(d.get("voice_stats_min_n", 15))))
        except (TypeError, ValueError):
            d["voice_stats_min_n"] = 15
        try:
            d["pipeline_stale_days"] = max(1, min(90, int(d.get("pipeline_stale_days", 7))))
        except (TypeError, ValueError):
            d["pipeline_stale_days"] = 7
        try:
            d["max_bounce_retries"] = max(0, min(5, int(d.get("max_bounce_retries", 3))))
        except (TypeError, ValueError):
            d["max_bounce_retries"] = 3
        d["imap_enabled"] = bool(d.get("imap_enabled", False))
        d["imap_ssl"] = bool(d.get("imap_ssl", True))
        d["send_window_advisory"] = bool(d.get("send_window_advisory", True))
        try:
            d["imap_port"] = max(1, min(65535, int(d.get("imap_port", 993))))
        except (TypeError, ValueError):
            d["imap_port"] = 993
        try:
            d["imap_poll_minutes"] = max(0, min(1440, int(d.get("imap_poll_minutes", 0))))
        except (TypeError, ValueError):
            d["imap_poll_minutes"] = 0
        mb = d.get("imap_mailboxes") or ["INBOX"]
        d["imap_mailboxes"] = [str(m) for m in mb if isinstance(m, str) and m.strip()] or ["INBOX"]
        if d.get("voice_learning_routing") not in ("off", "suggest", "auto"):
            d["voice_learning_routing"] = "off"
        try:
            d["voice_explore_epsilon"] = max(0.0, min(1.0, float(d.get("voice_explore_epsilon", 0.1))))
        except (TypeError, ValueError):
            d["voice_explore_epsilon"] = 0.1
        # ---- Layer 4 content-learning fields ----
        if d.get("voice_learning_mode") not in ("off", "suggest", "auto"):
            d["voice_learning_mode"] = "auto"
        try:
            d["voice_learning_min_edits"] = max(1, min(100, int(d.get("voice_learning_min_edits", 5))))
        except (TypeError, ValueError):
            d["voice_learning_min_edits"] = 5
        try:
            d["voice_learning_max_examples"] = max(0, min(20, int(d.get("voice_learning_max_examples", 5))))
        except (TypeError, ValueError):
            d["voice_learning_max_examples"] = 5
        try:
            d["voice_learning_cooldown_hours"] = max(0, min(720, int(d.get("voice_learning_cooldown_hours", 12))))
        except (TypeError, ValueError):
            d["voice_learning_cooldown_hours"] = 12
        d["voice_learning_promote"] = bool(d.get("voice_learning_promote", False))
        d["voice_learning_reflection_model"] = str(d.get("voice_learning_reflection_model") or "").strip()
        cp = d.get("cost_prices")
        if not isinstance(cp, dict):
            d["cost_prices"] = {}
        d["eml_dir"] = str(d.get("eml_dir") or "").strip()
        return d


def load_settings() -> Settings:
    s = Settings()
    if SETTINGS_FILE.exists():
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if hasattr(s, k) and v is not None:
                    setattr(s, k, v)
        except Exception:
            pass  # a corrupt settings file falls back to defaults, never crashes launch
    # env overrides (highest precedence)
    if os.environ.get("PARIS_PROVIDER"):
        s.provider = os.environ["PARIS_PROVIDER"]
    if os.environ.get("PARIS_DEFAULT_VOICE"):
        s.default_voice = os.environ["PARIS_DEFAULT_VOICE"]
    if os.environ.get("PARIS_EML_DIR"):
        s.eml_dir = os.environ["PARIS_EML_DIR"]
    if os.environ.get("PARIS_GEMINI_MODEL"):
        s.gemini_model = os.environ["PARIS_GEMINI_MODEL"]
    if os.environ.get("PARIS_COMPOSE_MODEL"):
        s.compose_model = os.environ["PARIS_COMPOSE_MODEL"]
    if os.environ.get("PARIS_HELPER_MODEL"):
        s.helper_model = os.environ["PARIS_HELPER_MODEL"]
    if os.environ.get("PARIS_RESEARCH_THINKING_LEVEL"):
        s.research_thinking_level = os.environ["PARIS_RESEARCH_THINKING_LEVEL"]
    if os.environ.get("PARIS_COMPOSE_THINKING_LEVEL"):
        s.compose_thinking_level = os.environ["PARIS_COMPOSE_THINKING_LEVEL"]
    if os.environ.get("PARIS_FALLBACK_MODEL"):
        s.fallback_model = os.environ["PARIS_FALLBACK_MODEL"]
    if os.environ.get("PARIS_ANTHROPIC_MODEL"):
        s.anthropic_model = os.environ["PARIS_ANTHROPIC_MODEL"]
    if os.environ.get("PARIS_PORT"):
        try:
            s.port = int(os.environ["PARIS_PORT"])
        except ValueError:
            pass
    if s.provider not in VALID_PROVIDERS:
        s.provider = "gemini"
    return s


def atomic_write_text(path: Path, text: str) -> None:
    """Crash-safe write: write to a temp file in the SAME directory, fsync, then
    os.replace() (atomic on POSIX and Windows). Guarantees a reader — or a git commit
    running concurrently for device sync — never observes a half-written file."""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.fspath(path.parent), prefix=".tmp-",
                               suffix=(path.suffix or ".tmp"))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_settings(s: Settings) -> None:
    try:
        atomic_write_text(SETTINGS_FILE, json.dumps(s.sanitized(), indent=2))
    except OSError as e:
        raise RuntimeError(f"Permission or disk error writing to {SETTINGS_FILE}: {e}. "
                           "If on macOS, check ownership via 'sudo chown -R $(whoami) ~/.paris_outreach'") from e


# ---- per-launch security token ---------------------------------------------
# Minted once per process. The served HTML embeds it; every /api/* request must present it.
SESSION_TOKEN = secrets.token_urlsafe(32)
