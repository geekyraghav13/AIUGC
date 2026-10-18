import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["PEXELS_API_KEY"] = ""      # tests never call stock APIs
os.environ["PIXABAY_API_KEY"] = ""
os.environ["MAGNIFIC_API_KEY"] = ""    # …or Magnific


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Isolated SQLite DB per test."""
    from engine import db

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    db.reset_engine()
    yield db
    db.reset_engine()


@pytest.fixture()
def brief():
    from engine.brief import load_brief

    return load_brief(ROOT / "data" / "sample_briefs" / "fintech.json")


PAID_PROFILE = {"monthly_credit_cap": 18000, "per_video_credit_cap": 450, "allow_paid_revisions": True,
                "modes": {"faceless": {"estimate": 100, "cap": 120}, "images_vo": {"estimate": 250, "cap": 350},
                          "character": {"estimate": 450, "cap": 550}, "ai_character": {"estimate": 1400, "cap": 1600}}}


@pytest.fixture(autouse=True)
def paid_profile(request):
    """The shipped config is free-only (cost policy). Most tests exercise the paid Magnific paths, so they run under the
    old paid settings; tests marked `shipped_config` see the real file."""
    import copy

    from engine.settings import load_yaml

    if request.node.get_closest_marker("shipped_config"):
        yield
        return
    m = load_yaml("production")["magnific"]
    saved = copy.deepcopy(m)
    m.update(copy.deepcopy(PAID_PROFILE))
    for k in ("voice", "hook_clip", "scene_image"):
        m[k]["enabled"] = True
    yield
    m.clear()
    m.update(saved)
