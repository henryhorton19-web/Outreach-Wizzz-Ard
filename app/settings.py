"""App-level settings (NOT the engine's config.py, which is content-only).

Everything here is orchestration configuration: which model provider to use, the model IDs
(configurable and overridable, because provider model IDs drift and must be verifiable at run
time rather than trusted from a hardcoded string), search caps, concurrency, and paths.

Precedence for any setting: environment variable  >  settings.json in the user data dir  >
the documented default below. Model IDs in particular are meant to be changed in Settings.

# Model choices below define standard standing policy:
research on 2.5 Flash, compose on 2.5 Pro; Claude available as the optional provider.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, asdict, field
from pathlib import Path


# ---- env fallback helper ---------------------------------------------------

_LEGACY_PREFIX = "PARIS_"
_legacy_warned = set()

def _env(name: str) -> str | None:
    """WIZZARD_<name>, falling back to the deprecated PARIS_<name>."""
    v = os.environ.get("WIZZARD_" + name)
    if v:
        return v
    v = os.environ.get(_LEGACY_PREFIX + name)
    if v and name not in _legacy_warned:
        _legacy_warned.add(name)
        import sys
        print(f"  [settings] {_LEGACY_PREFIX}{name} is deprecated; use WIZZARD_{name}",
              file=sys.stderr)
    return v or None


# ---- paths -----------------------------------------------------------------

def _data_root() -> Path:
    """Per-user writable dir for batch JSON, caches, audit records, settings.
    Windows: %APPDATA%\\OutreachWizzard ; else ~/.outreach_wizzard . Overridable via WIZZARD_DATA_DIR / PARIS_DATA_DIR."""
    override = _env("DATA_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        target = Path(base) / "OutreachWizzard"
        legacy = Path(base) / "ParisOutreach"
    else:
        target = Path.home() / ".outreach_wizzard"
        legacy = Path.home() / ".paris_outreach"
    if not target.exists() and legacy.exists():
        import sys
        print(f"  [settings] Notice: Legacy data directory found at {legacy}. "
              f"If you wish to migrate your data, copy its contents to {target}.",
              file=sys.stderr)
    return target


def _default_outbox_dir() -> Path:
    """Staged .eml files live under the data dir. Override with the eml_dir setting
    (or WIZZARD_EML_DIR) to route them to a synced folder instead."""
    return DATA_DIR / "outbox"


DATA_DIR = _data_root()
CACHE_DIR = DATA_DIR / "caches"
BATCH_DIR = DATA_DIR / "batches"
AUDIT_DIR = DATA_DIR / "audit"
VOICES_DIR = DATA_DIR / "voices"
VOICE_HISTORY_DIR = DATA_DIR / "voice_history"   # per-voice snapshots for rollback (Layer 4)
SOURCING_PROMPTS_DIR = DATA_DIR / "sourcing_prompts"
ATTACH_DIR = DATA_DIR / "attachments"
OUTBOX_DIR = _default_outbox_dir()      # staged .eml files; defaults to DATA_DIR/outbox
SETTINGS_FILE = DATA_DIR / "settings.json"
DRAFT_JOBS_FILE = DATA_DIR / "draft_jobs.json"

for _d in (DATA_DIR, CACHE_DIR, BATCH_DIR, AUDIT_DIR, VOICES_DIR, VOICE_HISTORY_DIR,
           SOURCING_PROMPTS_DIR, ATTACH_DIR, OUTBOX_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def get_outbox_dir() -> Path:
    try:
        st = load_settings()
        if st.eml_dir and st.eml_dir.strip():
            p = Path(st.eml_dir.strip()).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            return p
    except Exception:
        pass
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    return OUTBOX_DIR


def get_outbox_helper_dir() -> Path:
    d = get_outbox_dir().parent / "outbox helper"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Bumped when shipped seed data changes in a way that should replace stale files in an
# existing data directory. Never-overwrite seeding is right for a user's own edits and
# wrong for a file inherited from an earlier build, and patching one file at a time did
# not scale: the profile was fixed and the sourcing presets were not.
BUILD_VERSION = "2026-08-1"


def _build_marker_path():
    return DATA_DIR / "build_version.json"


def _last_seeded_build() -> str:
    import json as _json
    try:
        return str(_json.loads(_build_marker_path().read_text(encoding="utf-8")).get("build") or "")
    except Exception:
        return ""


def _write_build_marker() -> None:
    import json as _json
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _build_marker_path().write_text(
            _json.dumps({"build": BUILD_VERSION}, indent=2), encoding="utf-8")
    except Exception:
        pass


# Historical migration aid, not a general rule. These phrases identify sourcing presets
# written for an earlier single-operator job-search use of this tool, which are wrong for
# any current mandate. Deliberately narrow: a false positive moves a user's own work
# aside. Do NOT extend this list to catch more files; report a miss instead.
_STALE_MARKERS = (
    "sciences po", "remote-english", "remote english", "part time", "part-time",
    "in paris or", "hours a week", "on exchange", "looking for a role",
)


def _looks_stale(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _STALE_MARKERS)


def _migrate_stale_sourcing_prompts() -> None:
    """Move superseded presets aside once, so shipped presets can seed.

    Backed up rather than deleted, and only on a marker match, so a preset the user
    wrote themselves is never touched.
    """
    import json as _json
    try:
        if not SOURCING_PROMPTS_DIR.exists():
            return
        for f in list(SOURCING_PROMPTS_DIR.glob("*.json")):
            try:
                d = _json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            blob = " ".join(str(d.get(k) or "") for k in
                            ("criteria_text", "exclude_notes", "display_name"))
            if not _looks_stale(blob):
                continue
            backup = f.with_suffix(".json.stale.bak")
            if not backup.exists():
                backup.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            f.unlink()
    except Exception:
        pass


def ensure_seeded() -> None:
    """Bootstrap-once PER KIND: if the store has ZERO items of a given kind, insert that kind's
    shipped starter items.
    """
    # Runs once per build, before any seeding, so migrated-away files are replaced by
    # the shipped versions in the same pass.
    _stale_check_needed = _last_seeded_build() != BUILD_VERSION
    if _stale_check_needed:
        _migrate_stale_sourcing_prompts()

    import json as _json
    import shutil
    pkg = Path(__file__).resolve().parent

    # 1. Voices
    try:
        existing_v = []
        for p in VOICES_DIR.glob("*.json"):
            try:
                existing_v.append(_json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
    except Exception:
        existing_v = []

    def _have_voice(kind: str) -> bool:
        return any((v.get("kind", "outreach") or "outreach") == kind for v in existing_v)

    v_plan = [
        pkg / "seed_voices",
        pkg / "seed_voices_local",
        pkg / "seed_followup_voices",
    ]
    for seed_dir in v_plan:
        if not seed_dir.exists():
            continue
        for src in seed_dir.glob("*.json"):
            target = VOICES_DIR / src.name
            if not target.exists():
                try:
                    shutil.copyfile(src, target)
                except Exception:
                    pass

    # 2. Custom Sourcing Prompts
    # Per file, not all-or-nothing. This was `if not existing_sp:`, so a single
    # user-created preset stopped every shipped preset from ever seeding. Same
    # never-overwrite class as the profile and the voices.
    sp_dirs = [pkg / "seed_sourcing_prompts", pkg / "seed_sourcing_prompts_local"]
    for sp_dir in sp_dirs:
        if not sp_dir.exists():
            continue
        for src in sp_dir.glob("*.json"):
            target = SOURCING_PROMPTS_DIR / src.name
            if not target.exists():
                try:
                    SOURCING_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, target)
                except Exception:
                    pass

    if _stale_check_needed:
        _write_build_marker()


ensure_seeded()


# ---- provider / model defaults ---------------------
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


    # ---- Phase 6b: bounce re-draft ----
    # Retries walk the ranked ladder: the current person's remaining address formats first, then a
    # DIFFERENT PERSON (an alt contact). Default 3 so at least one alternate person is reached.
    max_bounce_retries: int = 3                   # <= ladder length; exhaustion -> bounced_exhausted

    # ---- Phase 6c: send-window advisory ----
    send_window_advisory: bool = True             # non-blocking "consider staging Monday AM" hint

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

    # ---- Plan 26: self-learning (exemplar) voices ----
    # Everything here is edit-grounded. There is deliberately no reply-rate knob.
    exemplar_enabled: bool = True             # master switch for learning="exemplar" voices
    exemplar_corpus_cap: int = 200            # max stored exemplars per voice (evicted by value)
    exemplar_min_for_induction: int = 2       # exemplars needed before a template is induced
    exemplar_holes_k: int = 2                 # local exemplars injected per hole at compose time
    exemplar_support_promote: int = 2         # exemplars a span must appear in to become skeleton
    exemplar_freeze_window: int = 4           # turns of rising effort that freeze induction
    exemplar_novelty_max: float = 0.72        # max n-gram overlap with a recent sent email
    exemplar_recalibrate_every: int = 8       # suggest a blank-box turn every N approvals (0 = off)

    # ---- Sourcing fields ("Find new targets") ----
    sourcing_enabled: bool = True
    sourcing_target_n: int = 10
    sourcing_max_candidates: int = 40
    sourcing_max_web_per_candidate: int = 2
    sourcing_budget_usd: float = 3.00
    sourcing_recency_days: int = 120
    sourcing_sources: list[str] = field(default_factory=lambda: ["techeu_funding_feed", "grounded_search"])
    sourcing_reject_expiry_days: int = 60

    # ---- Stage E: permanent contacted-exclusion layer ----
    # When enabled, _ingest_to_queue and automated sourcing check excluded.json before
    # adding a target. Unconditional write on approve_one remains even when disabled
    # (so the list stays current). Never gates a human override -- operator can always
    # manually add a target.
    exclusion_enabled: bool = True
    allow_org_voice_learning: bool = False   # G2: gates Layer-4 learning on org-audience voices

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
        d["send_window_advisory"] = bool(d.get("send_window_advisory", True))
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
        d["exemplar_enabled"] = bool(d.get("exemplar_enabled", True))
        for _k, _lo, _hi, _dflt in (("exemplar_corpus_cap", 10, 2000, 200),
                                    ("exemplar_min_for_induction", 1, 50, 2),
                                    ("exemplar_holes_k", 0, 6, 2),
                                    ("exemplar_support_promote", 1, 20, 2),
                                    ("exemplar_freeze_window", 2, 50, 4),
                                    ("exemplar_recalibrate_every", 0, 100, 8)):
            try:
                d[_k] = max(_lo, min(_hi, int(d.get(_k, _dflt))))
            except (TypeError, ValueError):
                d[_k] = _dflt
        try:
            d["exemplar_novelty_max"] = max(0.30, min(0.99, float(d.get("exemplar_novelty_max", 0.72))))
        except (TypeError, ValueError):
            d["exemplar_novelty_max"] = 0.72
        d["voice_learning_reflection_model"] = str(d.get("voice_learning_reflection_model") or "").strip()
        cp = d.get("cost_prices")
        if not isinstance(cp, dict):
            d["cost_prices"] = {}
        d["eml_dir"] = str(d.get("eml_dir") or "").strip()

        # ---- Sourcing fields ----
        d["sourcing_enabled"] = bool(d.get("sourcing_enabled", True))
        try:
            d["sourcing_target_n"] = max(1, min(100, int(d.get("sourcing_target_n", 10))))
        except (TypeError, ValueError):
            d["sourcing_target_n"] = 10
        try:
            d["sourcing_max_candidates"] = max(1, min(200, int(d.get("sourcing_max_candidates", 40))))
        except (TypeError, ValueError):
            d["sourcing_max_candidates"] = 40
        try:
            d["sourcing_max_web_per_candidate"] = max(0, min(10, int(d.get("sourcing_max_web_per_candidate", 2))))
        except (TypeError, ValueError):
            d["sourcing_max_web_per_candidate"] = 2
        try:
            d["sourcing_budget_usd"] = max(0.1, min(50.0, float(d.get("sourcing_budget_usd", 3.00))))
        except (TypeError, ValueError):
            d["sourcing_budget_usd"] = 3.00
        try:
            d["sourcing_recency_days"] = max(1, min(365, int(d.get("sourcing_recency_days", 120))))
        except (TypeError, ValueError):
            d["sourcing_recency_days"] = 120
        srcs = d.get("sourcing_sources") or ["techeu_funding_feed", "grounded_search"]
        if not isinstance(srcs, list):
            srcs = ["techeu_funding_feed", "grounded_search"]
        d["sourcing_sources"] = [str(s) for s in srcs if isinstance(s, str) and s.strip()] or ["techeu_funding_feed", "grounded_search"]
        try:
            d["sourcing_reject_expiry_days"] = max(1, min(365, int(d.get("sourcing_reject_expiry_days", 60))))
        except (TypeError, ValueError):
            d["sourcing_reject_expiry_days"] = 60
        return d


def load_settings() -> Settings:
    s = Settings()
    if SETTINGS_FILE.exists():
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if hasattr(s, k) and v is not None:
                        setattr(s, k, v)
        except Exception:
            pass  # a corrupt settings file falls back to defaults, never crashes launch
    # env overrides (highest precedence)
    if (v := _env("PROVIDER")): s.provider = v
    if (v := _env("DEFAULT_VOICE")): s.default_voice = v
    if (v := _env("EML_DIR")): s.eml_dir = v
    if (v := _env("GEMINI_MODEL")): s.gemini_model = v
    if (v := _env("COMPOSE_MODEL")): s.compose_model = v
    if (v := _env("HELPER_MODEL")): s.helper_model = v
    if (v := _env("RESEARCH_THINKING_LEVEL")): s.research_thinking_level = v
    if (v := _env("COMPOSE_THINKING_LEVEL")): s.compose_thinking_level = v
    if (v := _env("FALLBACK_MODEL")): s.fallback_model = v
    if (v := _env("ANTHROPIC_MODEL")): s.anthropic_model = v
    if (v := _env("PORT")):
        try:
            s.port = int(v)
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
        for _attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if _attempt == 4:
                    raise
                import time
                time.sleep(0.02)
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
                           "If on macOS, check ownership via 'sudo chown -R $(whoami) ~/.outreach_wizzard'") from e


# ---- per-launch security token ---------------------------------------------
# Minted once per process. The served HTML embeds it; every /api/* request must present it.
SESSION_TOKEN = secrets.token_urlsafe(32)
