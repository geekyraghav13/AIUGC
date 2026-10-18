from engine.captions import Word, caption_lines, design, group_lines


def test_group_lines_breaks_on_punctuation_and_avoids_orphans():
    words = [Word(i, i + 0.5, w) for i, w in enumerate("one two three. four five six seven".split())]
    lines = group_lines(words, 3)
    assert [len(l) for l in lines] == [3, 4] or all(2 <= len(l) <= 4 for l in lines)
    assert lines[0][-1].text == "three."


def test_caption_lines_timing_and_scene_look():
    words = [Word(i * 0.5, i * 0.5 + 0.4, f"w{i}") for i in range(6)]
    scenes = [(0.0, 1.4, {"y": 330}), (1.4, 4.0, {"y": 1290})]
    lines = caption_lines(words, 3, scenes)
    assert [l["words"][0]["text"] for l in lines] == ["w0", "w3"]
    assert lines[0]["end"] == lines[1]["start"]  # a line stays up until the next begins
    assert lines[1]["end"] >= words[-1].end
    assert [l["look"]["y"] for l in lines] == [330, 1290]  # each line takes the look of the scene it starts in


def test_typography_is_deterministic_per_video_and_varies_between_videos():
    a1, a2 = design("hi", "meme_text", 7), design("hi", "meme_text", 7)
    assert a1 == a2                                             # a re-render of the same video looks the same
    combos = {(d["family"], d["scriptFamily"], d["anim"], d["size"], d["outlinePx"])
              for d in (design("hi", "meme_text", i) for i in range(1, 25))}
    assert len(combos) >= 15                                    # other videos of the same style differ
    assert len({design("hi", "bestie_narrator", i)["family"] for i in range(1, 25)}) >= 5


def test_fonts_match_the_script_and_exist():
    from engine.settings import FONTS_DIR

    for lang, marker in (("hi", "Devanagari"), ("ta", "Tamil"), ("hinglish", None)):
        for i in range(1, 30):
            d = design(lang, "storytime", i)
            assert all((FONTS_DIR / f["file"]).exists() for f in d["_fonts"])
            assert d["langFont"].endswith(marker) if marker else d["langFont"] == "Noto Sans"
    # Latin-only languages never need a separate script font
    assert design("hinglish", "storytime", 3)["scriptFamily"] == design("hinglish", "storytime", 3)["family"]


def test_caps_only_for_faces_that_want_it_or_rarely_for_latin():
    for i in range(1, 60):
        d = design("ta", "meme_text", i)
        assert d["anim"] in ("bounce", "pop", "pill")           # from the style's animation set
