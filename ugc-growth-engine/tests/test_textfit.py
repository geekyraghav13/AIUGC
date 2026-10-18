import numpy as np
from PIL import Image

from engine import textfit


def frame(busy_band: str | None = None, base=(40, 60, 90)) -> Image.Image:
    """Calm flat frame; optionally make one band noisy."""
    w, h = textfit.SAMPLE
    a = np.full((h, w, 3), base, dtype=np.uint8)
    if busy_band:
        s = textfit._band_slice(busy_band)
        a[s] = np.random.default_rng(1).integers(0, 255, a[s].shape, dtype=np.uint8)
    return Image.fromarray(a)


def pal(seed: int = 1, base=(40, 60, 90)) -> textfit.Palette:
    return textfit.video_palette([frame(base=base)], "FFB300", seed)


def test_text_moves_to_calmer_band():
    look = textfit.choose([[frame("lower")]], pal(), prefer="lower")[0]
    assert look.band != "lower"  # the preferred band is noisy, so text goes elsewhere


def test_previous_band_kept_unless_clearly_calmer():
    looks = textfit.choose([[frame()], [frame()]], pal(), prefer="upper")
    assert looks[0].band == looks[1].band == "upper"  # equally calm → no jumping between scenes


def test_hook_avoid_and_allowed():
    look = textfit.choose([[frame()]], pal(), prefer="top", allowed=("top", "upper"), avoid={0: "top"})[0]
    assert look.band == "upper"


def test_screenshot_scene_sits_under_the_phone_with_a_plate():
    look = textfit.choose([[]], pal(), prefer="lower", forced=["shot"])[0]
    assert look.y == textfit.SHOT_Y and look.plate


def test_one_palette_per_video_never_changes_between_scenes():
    p = pal(seed=5)
    looks = textfit.choose([[frame()], [frame(base=(235, 235, 240))], [frame("lower", base=(90, 20, 20))]], p,
                           prefer="lower")
    assert {(l.primary, l.outline, l.highlight) for l in looks} == {(p.primary, p.outline, p.accent)}


def test_palettes_differ_between_videos():
    pals = [textfit.video_palette([frame(base=(120, 60, 200))], "FFB300", seed) for seed in range(1, 13)]
    assert len({p.style for p in pals}) >= 3          # placeholder styles rotate
    assert len({p.accent for p in pals}) >= 5         # accents vary too
    assert len({(p.style, p.accent) for p in pals}) >= 8


def test_palette_follows_the_footage_hue():
    warm = textfit.video_palette([frame(base=(220, 80, 30))], "FFB300", 3)
    cool = textfit.video_palette([frame(base=(30, 90, 220))], "FFB300", 3)
    assert warm.accent != cool.accent and warm.style == cool.style  # same seed: same style, footage-driven colour


def test_light_card_accent_is_readable_on_white():
    for seed in range(1, 40):
        p = textfit.video_palette([frame(base=(60, 160, 90))], "FFB300", seed)
        if p.style == "light":
            assert textfit.contrast(textfit._rel_lum_hex(p.accent), 1.0) >= 3.0
            assert p.primary == "111111"
            return
    raise AssertionError("no light-card palette produced")


def test_outline_style_gets_a_plate_only_on_bright_footage():
    seed = next(s for s in range(1, 40) if pal(s).style == "outline")
    dark = textfit.choose([[frame()]], pal(seed), prefer="lower")[0]
    bright = textfit.choose([[frame(base=(235, 235, 240))]], pal(seed), prefer="lower")[0]
    assert dark.plate is None and bright.plate


def test_accent_is_readable_on_dark_surfaces_for_every_hue():
    for r, g, b in ((30, 60, 220), (120, 40, 200), (220, 40, 40), (40, 200, 90), (200, 200, 40)):
        for seed in range(1, 25):
            p = textfit.video_palette([frame(base=(r, g, b))], "FFB300", seed)
            if p.style != "light":
                assert textfit.contrast(textfit._rel_lum_hex(p.accent), 0.03) >= 5.0, (r, g, b, seed, p.accent)
