"""SQLite data model (sqlmodel)."""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from sqlalchemy import JSON, Column, event
from sqlmodel import Field, Session, SQLModel, create_engine, select

from engine.settings import ROOT


def _now() -> datetime:
    return datetime.now(timezone.utc)


class App(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    category: str = ""
    brief: dict = Field(default_factory=dict, sa_column=Column(JSON))
    play_store_id: Optional[str] = None
    icon_path: Optional[str] = None
    screenshots: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class Understanding(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True)
    personas: list = Field(default_factory=list, sa_column=Column(JSON))
    pain_points: list = Field(default_factory=list, sa_column=Column(JSON))
    loved_features: list = Field(default_factory=list, sa_column=Column(JSON))
    user_phrases: list = Field(default_factory=list, sa_column=Column(JSON))
    key_messages: list = Field(default_factory=list, sa_column=Column(JSON))
    india_moments: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class Concept(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True)
    generation: int = 1
    title: str
    persona: str = ""
    pain_point: str = ""
    message: str = ""
    india_scenario: str = ""
    format: str = ""
    market_evidence: list = Field(default_factory=list, sa_column=Column(JSON))


class Creative(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True)
    concept_id: Optional[int] = Field(default=None, index=True)
    generation: int = Field(default=1, index=True)
    parent_creative_id: Optional[int] = None
    # tags
    language: str = ""
    hook: str = ""
    hook_type: str = ""
    format: str = ""
    creator_style: str = ""
    persona: str = ""
    message: str = ""
    cta_type: str = "download"
    length_bucket: str = ""
    visual_mode: str = ""   # faceless | images_vo | character | ai_character
    # content
    script: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    post_caption: str = ""
    hashtags: list = Field(default_factory=list, sa_column=Column(JSON))
    predicted_score: float = 0.0
    compliance_status: str = "unchecked"  # unchecked/pass/flagged/approved/rejected
    compliance_issues: list = Field(default_factory=list, sa_column=Column(JSON))
    status: str = "draft"  # hook_only/draft/approved/rendered/published/failed/rejected
    tracking_slug: Optional[str] = Field(default=None, index=True)
    origin: str = "explore"  # explore/exploit
    selected_for_render: bool = False
    market_evidence: list = Field(default_factory=list, sa_column=Column(JSON))
    style_signature: str = ""  # the video's text look (fonts · palette · layouts); unique within a generation
    created_at: datetime = Field(default_factory=_now)


class Video(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    creative_id: int = Field(index=True)
    path: str
    thumbnail_path: Optional[str] = None
    duration_s: float = 0.0
    render_seconds: float = 0.0
    attribution: list = Field(default_factory=list, sa_column=Column(JSON))


class Metric(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    creative_id: int = Field(index=True)
    source: str = "simulated"  # real/simulated
    platform: str = "instagram"
    date: str = ""
    impressions: int = 0
    views: int = 0
    views_3s: int = 0
    avg_watch_pct: float = 0.0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    clicks: int = 0
    installs: Optional[int] = None
    spend_inr: float = 0.0
    d1_retained: Optional[int] = None
    d7_retained: Optional[int] = None


class Insight(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True)
    generation: int = 1
    findings: dict = Field(default_factory=dict, sa_column=Column(JSON))
    narrative: str = ""
    created_at: datetime = Field(default_factory=_now)


class Ledger(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: Optional[str] = Field(default=None, index=True)
    step: str
    provider: str = ""
    seconds: float = 0.0
    cost_inr: float = 0.0
    paid_equivalent_inr: float = 0.0
    units: int = 1
    created_at: datetime = Field(default_factory=_now)


class SimTruth(SQLModel, table=True):
    """Hidden 'true' effects for the simulated market. The optimizer never reads this."""
    app_id: str = Field(primary_key=True)
    seed: int = 42
    effects: dict = Field(default_factory=dict, sa_column=Column(JSON))


# ---------------------------------------------------------------- D8 market research

class ResearchRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True)
    started_at: datetime = Field(default_factory=_now)
    finished_at: Optional[datetime] = None
    quota_used: int = 0
    gemini_calls: int = 0
    source: str = "youtube"  # youtube / mock
    status: str = "running"  # running/done/failed
    notes: str = ""


class Keyword(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True)
    run_id: int = Field(index=True)
    text: str
    language: str = ""
    source: str = "seed"  # seed/autocomplete
    autocomplete_hits: int = 0
    trends_interest: Optional[float] = None
    top10_views: Optional[int] = None
    searched: bool = False
    demand_score: float = 0.0


class MarketVideo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    platform: str = "youtube"
    video_id: str = Field(index=True)
    url: str = ""
    title: str = ""
    channel_id: str = ""
    channel_title: str = ""
    published_at: Optional[str] = None
    duration_s: int = 0
    views: int = 0
    likes: int = 0
    comments: int = 0
    baseline: Optional[float] = None
    outlier_score: Optional[float] = None
    market_score: Optional[float] = None
    keywords: list = Field(default_factory=list, sa_column=Column(JSON))
    relevant: bool = False
    selected: bool = False
    thumbnail_url: Optional[str] = None
    fetched_at: datetime = Field(default_factory=_now)


class MarketAnalysis(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    market_video_id: int = Field(index=True)
    analysis: dict = Field(default_factory=dict, sa_column=Column(JSON))
    model: str = ""
    created_at: datetime = Field(default_factory=_now)


class MarketPattern(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    dimension: str
    value: str
    videos: int = 0
    share: float = 0.0
    weighted_share: float = 0.0
    avg_outlier: float = 0.0
    lift: float = 0.0
    signal: str = "weak signal"  # pattern / weak signal
    examples: list = Field(default_factory=list, sa_column=Column(JSON))


class MarketPlaybook(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True)
    run_id: int = Field(index=True)
    playbook: dict = Field(default_factory=dict, sa_column=Column(JSON))
    narrative: str = ""
    created_at: datetime = Field(default_factory=_now)


class Persona(SQLModel, table=True):
    """A recurring AI character for one app. Protagonists act in `character` videos; presenters talk to camera in
    `ai_character` videos. Derived from the app's own audience and brand tone, never a real person."""
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True)
    key: str = Field(index=True)          # short slug, unique per app (e.g. "shop_owner")
    name: str = ""                        # screen name used in plans (not shown to viewers)
    role: str = "protagonist"             # protagonist | presenter
    gender: str = "female"
    age: str = ""
    languages: list = Field(default_factory=list, sa_column=Column(JSON))
    description: str = ""                 # who they are, in one or two lines
    look_prompt: str = ""                 # English portrait prompt (face, age, clothes, setting, light)
    voice_id: Optional[int] = None        # Magnific voice (same voice in every video)
    candidates: list = Field(default_factory=list, sa_column=Column(JSON))   # [{file, url, creation}]
    approved: dict = Field(default_factory=dict, sa_column=Column(JSON))     # {file, url, creation}
    library_id: Optional[str] = None      # Magnific character-library id (reference for every later image/clip)
    status: str = "proposed"              # proposed | candidates | approved | ready
    created_at: datetime = Field(default_factory=_now)


class Batch(SQLModel, table=True):
    """One iteration: 10 scripts the owner approves, then produced one video at a time."""
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True)
    generation: int = 1
    status: str = "scripts_ready"   # building | scripts_ready | approved | processing | done | stopped
    creative_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    mix: dict = Field(default_factory=dict, sa_column=Column(JSON))      # languages / modes and where they came from
    credits_estimate: int = 0        # Magnific credits for the approved scripts (typical per mode)
    credits_approved: int = 0        # hard ceiling the owner approved (estimate + margin)
    credits_spent: int = 0
    current_creative: Optional[int] = None
    notes: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    approved_at: Optional[datetime] = None


class ProductionRun(SQLModel, table=True):
    """One creative going through the production crew: director → editor → reviewers → QC → owner."""
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True)
    creative_id: int = Field(index=True)
    version: int = 0                     # latest rendered version (v1, v2, ...)
    stage: str = "director"              # director | assets | editor | review | qc | owner | done
    status: str = "running"              # running | waiting_assets | awaiting_owner | approved | rejected | failed
    plan: dict = Field(default_factory=dict, sa_column=Column(JSON))          # director's scene plan (patched by fixes)
    asset_requests: list = Field(default_factory=list, sa_column=Column(JSON))  # Magnific jobs + their status
    versions: list = Field(default_factory=list, sa_column=Column(JSON))      # [{version, path, thumb, qc_score, ...}]
    log: list = Field(default_factory=list, sa_column=Column(JSON))           # [{at, role, message}]
    qc_score: Optional[float] = None
    owner_note: str = ""
    credits_estimate: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ReviewTask(SQLModel, table=True):
    """A specific issue a reviewer raised, routed to the role that must act on it."""
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    version: int                          # version the issue was found in
    raised_by: str                        # text | visual | audio | brand | qc | owner
    assigned_to: str                      # director | editor | text | audio | assets | owner
    severity: str = "major"               # blocker | major | minor
    category: str = ""
    timecode: Optional[float] = None      # seconds into the video
    scene: Optional[int] = None
    issue: str = ""
    fix: dict = Field(default_factory=dict, sa_column=Column(JSON))  # machine-applicable action for the editor
    status: str = "open"                  # open | fixed | wont_fix | needs_human
    resolved_in: Optional[int] = None     # version that resolved it
    created_at: datetime = Field(default_factory=_now)


# Columns added after the first release: (table, column, SQL type). Applied to existing DBs on startup.
MIGRATIONS = [("concept", "market_evidence", "JSON"), ("creative", "market_evidence", "JSON"),
              ("marketvideo", "thumbnail_url", "VARCHAR"), ("creative", "visual_mode", "VARCHAR DEFAULT ''"),
              ("creative", "style_signature", "VARCHAR DEFAULT ''")]

_engine = None


def db_url() -> str:
    return os.getenv("DATABASE_URL") or f"sqlite:///{ROOT / 'engine.db'}"


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(db_url(), connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(_engine, "connect")
        def _pragmas(conn, _rec):  # noqa: ANN001
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        SQLModel.metadata.create_all(_engine)
        _migrate(_engine)
    return _engine


def _migrate(engine) -> None:  # noqa: ANN001
    with engine.begin() as conn:
        for table, col, typ in MIGRATIONS:
            cols = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
            if cols and col not in cols:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def reset_engine() -> None:
    """Forget the cached engine (used by tests that swap DATABASE_URL)."""
    global _engine
    _engine = None


@contextmanager
def session() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as s:
        yield s


def get_app(app_id: str) -> Optional[App]:
    with session() as s:
        return s.get(App, app_id)


def list_apps() -> list[App]:
    with session() as s:
        return list(s.exec(select(App)).all())


def latest_understanding(app_id: str) -> Optional[Understanding]:
    with session() as s:
        return s.exec(
            select(Understanding).where(Understanding.app_id == app_id).order_by(Understanding.id.desc())
        ).first()


def creatives(app_id: str, generation: Optional[int] = None, **filters: Any) -> list[Creative]:
    with session() as s:
        q = select(Creative).where(Creative.app_id == app_id)
        if generation is not None:
            q = q.where(Creative.generation == generation)
        for k, v in filters.items():
            q = q.where(getattr(Creative, k) == v)
        return list(s.exec(q.order_by(Creative.id)).all())


def current_generation(app_id: str) -> int:
    with session() as s:
        rows = s.exec(select(Creative.generation).where(Creative.app_id == app_id)).all()
    return max(rows) if rows else 0


def save(*objs: SQLModel) -> None:
    with session() as s:
        for o in objs:
            s.add(o)
        s.commit()
        for o in objs:
            s.refresh(o)


def reset_generations(app_id: str) -> None:
    """Delete understanding, concepts, creatives, videos, metrics and insights — keep market research."""
    import shutil

    from sqlmodel import delete

    from engine.settings import OUTPUTS_DIR

    with session() as s:
        ids = list(s.exec(select(Creative.id).where(Creative.app_id == app_id)).all())
        if ids:
            s.exec(delete(Metric).where(Metric.creative_id.in_(ids)))
            s.exec(delete(Video).where(Video.creative_id.in_(ids)))
        for model in (Creative, Concept, Understanding, Insight):
            s.exec(delete(model).where(model.app_id == app_id))
        s.exec(delete(SimTruth).where(SimTruth.app_id == app_id))
        s.commit()
    shutil.rmtree(OUTPUTS_DIR / app_id, ignore_errors=True)


def reset_app(app_id: str) -> None:
    """Delete all generated data for an app (keeps the App row/brief). Used for fresh demo runs."""
    import shutil

    from sqlmodel import delete

    from engine.settings import OUTPUTS_DIR

    with session() as s:
        ids = list(s.exec(select(Creative.id).where(Creative.app_id == app_id)).all())
        if ids:
            s.exec(delete(Metric).where(Metric.creative_id.in_(ids)))
            s.exec(delete(Video).where(Video.creative_id.in_(ids)))
        for model in (Creative, Concept, Understanding, Insight, Ledger):
            s.exec(delete(model).where(model.app_id == app_id))
        run_ids = list(s.exec(select(ResearchRun.id).where(ResearchRun.app_id == app_id)).all())
        if run_ids:
            vids = list(s.exec(select(MarketVideo.id).where(MarketVideo.run_id.in_(run_ids))).all())
            if vids:
                s.exec(delete(MarketAnalysis).where(MarketAnalysis.market_video_id.in_(vids)))
            for model in (MarketVideo, MarketPattern, Keyword):
                s.exec(delete(model).where(model.run_id.in_(run_ids)))
            s.exec(delete(MarketPlaybook).where(MarketPlaybook.app_id == app_id))
            s.exec(delete(ResearchRun).where(ResearchRun.app_id == app_id))
        s.exec(delete(SimTruth).where(SimTruth.app_id == app_id))
        s.commit()
    shutil.rmtree(OUTPUTS_DIR / app_id, ignore_errors=True)


def delete_app(app_id: str) -> None:
    """Remove an app completely: its brief, research, scripts, productions, characters and files.
    Spend-ledger rows stay, so the month's Gemini/Magnific budget still counts what was really spent."""
    import shutil

    from sqlmodel import delete

    from engine.settings import CACHE_DIR, DATA_DIR, OUTPUTS_DIR

    with session() as s:
        ledger = [r.model_dump() for r in s.exec(select(Ledger).where(Ledger.app_id == app_id)).all()]
    reset_app(app_id)                                    # generated data (also clears ledger rows → restored below)
    with session() as s:
        run_ids = list(s.exec(select(ProductionRun.id).where(ProductionRun.app_id == app_id)).all())
        if run_ids:
            s.exec(delete(ReviewTask).where(ReviewTask.run_id.in_(run_ids)))
        for model in (ProductionRun, Batch, Persona):
            s.exec(delete(model).where(model.app_id == app_id))
        s.exec(delete(App).where(App.id == app_id))
        for row in ledger:
            s.add(Ledger(**row))
        s.commit()
    shutil.rmtree(DATA_DIR / "apps" / app_id, ignore_errors=True)
    shutil.rmtree(OUTPUTS_DIR / app_id, ignore_errors=True)
    (CACHE_DIR / "progress" / f"{app_id}.json").unlink(missing_ok=True)
