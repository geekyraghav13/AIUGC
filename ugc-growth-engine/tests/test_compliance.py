from engine.compliance import rule_check
from engine.localize import check_language


def test_fake_testimonial_flagged(brief):
    issues = rule_check("Hi, I'm Priya from Pune and this app changed my life!", brief)
    assert any("testimonial" in i for i in issues)


def test_guarantee_and_earning_flagged(brief):
    assert any("guarantee" in i for i in rule_check("100% guaranteed results", brief))
    assert any("earning" in i for i in rule_check("Earn ₹500 daily with this app", brief))


def test_invented_stat_flagged(brief):
    assert any("statistic" in i for i in rule_check("90% log ye galti karte hain", brief))


def test_claims_to_avoid(brief):
    assert any("claims_to_avoid" in i for i in rule_check("Guaranteed savings every month", brief))


def test_clean_hooks_pass(brief):
    for t in ["Month-end pe paisa kahan gaya, pata hai?", "சம்பளம் வந்து பத்து நாள்ல காலியா?",
              "सैलरी आई और दस दिन में खत्म?"]:
        assert rule_check(t, brief) == [], t


def test_language_script_checks():
    assert check_language("Month-end pe paisa kahan gaya?", "hinglish")[0]
    assert not check_language("महीने के आखिर में पैसे", "hinglish")[0]
    assert check_language("சம்பளம் வந்து UPI ல", "ta")[0]
    assert not check_language("Salary aayi aur gayab", "ta")[0]
    assert check_language("महीने के आखिर में UPI app", "hi")[0]
