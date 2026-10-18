import pytest

from engine.llm import extract_json, render_prompt


def test_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_fenced_json_with_prose():
    text = 'Sure! Here you go:\n```json\n{"hooks": [{"hook": "hi"}]}\n```\nThanks'
    assert extract_json(text) == {"hooks": [{"hook": "hi"}]}


def test_trailing_commas_and_leading_prose():
    assert extract_json('Result: {"a": [1, 2,], "b": {"c": 3,},}') == {"a": [1, 2], "b": {"c": 3}}


def test_unicode_preserved():
    assert extract_json('{"hook": "சம்பளம் வந்து?"}')["hook"] == "சம்பளம் வந்து?"


def test_no_json_raises():
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_prompt_substitution_keeps_json_braces():
    p = render_prompt("compliance", claims_to_avoid="x", facts={"a": 1}, scripts=[])
    assert '{"reviews": [{"id": 0, "pass": true, "issues": [""]}]}' in p
    assert '"a": 1' in p
