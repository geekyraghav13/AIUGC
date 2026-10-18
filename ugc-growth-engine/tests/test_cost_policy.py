"""Cost policy: the shipped config buys nothing from Magnific unless the owner switches it on."""
import pytest

pytestmark = pytest.mark.shipped_config


def test_shipped_config_is_free_by_default():
    from engine.settings import load_yaml

    m = load_yaml("production")["magnific"]
    assert m["voice"]["enabled"] is False
    assert m["scene_image"]["enabled"] is False
    assert m["hook_clip"]["enabled"] is False
    assert m["allow_paid_revisions"] is False
    assert all(v["estimate"] == 0 and v["cap"] == 0 for v in m["modes"].values())
    assert m["monthly_credit_cap"] <= 600


def test_approved_ceiling_has_no_margin():
    from engine import batch

    assert batch.MARGIN == 0.0


def test_revision_rounds_cannot_order_paid_assets():
    from engine.production import assets

    assert assets.add_request(None, "voice", 0) is None       # refused before it touches the run
