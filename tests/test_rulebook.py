from pathlib import Path
from media.rulebook import load, hard_hits

RB = load(Path(__file__).resolve().parents[1] / "brand" / "rulebook.yaml")

def test_loads():
    assert RB.disclaimer and len(RB.shots) >= 4 and "cure" in RB.banned_words

def test_hits_whole_words_only():
    assert hard_hits("This will CURE your insomnia", RB) == ["cure"]
    assert hard_hits("A secure night", RB) == []
    assert hard_hits("Guaranteed results, doctor recommended", RB) == ["doctor recommended", "guaranteed"]

def test_hits_phrases_ending_in_a_symbol():
    # "100%" ends in a non-word character, where a plain \b never matches
    assert hard_hits("100% of people", RB) == ["100%"]
    assert hard_hits("a 1100%x thing", RB) == []


def test_the_spec_bans_are_in_the_list():
    for w in ("treat", "treats", "treatment", "before/after", "price", "prices", "$", "today only", "hurry", "act now", "last chance"):
        assert w in RB.banned_words, w
    assert hard_hits("Treat yourself to treatment", RB) == ["treat", "treatment"]
    assert hard_hits("Before/after photos, act now!", RB) == ["act now", "before/after"]
    assert hard_hits("Hurry, today only: last chance", RB) == ["hurry", "last chance", "today only"]
    assert hard_hits("Great prices", RB) == ["prices"]
    assert hard_hits("A retreat", RB) == []


def test_no_claim_or_shot_has_a_hard_hit():
    for text in RB.claims + RB.shots:
        assert hard_hits(text, RB) == [], text


def test_phrases_match_across_whitespace_and_hyphens():
    assert hard_hits("doctor\nrecommended", RB) == ["doctor recommended"]
    assert hard_hits("doctor   recommended", RB) == ["doctor recommended"]
    assert hard_hits("Clinically-proven", RB) == ["clinically proven"]
    assert hard_hits("clinically\t-\nproven", RB) == ["clinically proven"]
    assert hard_hits("risk free", RB) == ["risk-free"]
    assert hard_hits("doctorrecommended", RB) == []


def test_a_symbol_phrase_matches_inside_a_number():
    assert hard_hits("only $49 a month", RB) == ["$"]
    assert hard_hits("US$49", RB) == ["$"]
    assert hard_hits("49 dollars", RB) == []
