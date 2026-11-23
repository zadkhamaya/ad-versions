import pytest
from media.naming import version_name, matrix, FORMATS

def test_example():
    assert version_name("Brand", "sleep launch", "H1", 15, "9X16") == "BRAND_SLEEPLAUNCH_H1_15S_9X16_EN_V01.mp4"

def test_charset():
    assert version_name("Ré-st!", "a/b", "H2", 6, "1X1", v=12) == "RST_AB_H2_6S_1X1_EN_V12.mp4"

def test_matrix_is_18():
    m = matrix()
    assert len(m) == 18 and len(set(m)) == 18

@pytest.mark.parametrize("bad", [("H3", 15, "9X16"), ("H1", 20, "9X16"), ("H1", 15, "4X5")])
def test_refuses(bad):
    with pytest.raises(ValueError):
        version_name("B", "C", *bad)


def test_version_id_and_parse_round_trip_over_the_matrix():
    from media.naming import version_id, parse_version
    ids = [version_id(*m) for m in matrix()]
    assert len(set(ids)) == 18 and "H1_15S_9X16" in ids
    for m in matrix():
        assert parse_version(version_id(*m)) == m


@pytest.mark.parametrize("bad", ["", "H1", "H3_15S_9X16", "H1_7S_9X16", "H1_15S_4X5", "h1_15s_9x16", "H1_15S_9X16_EN", "H1_15_9X16",
                                 " H1_15S_9X16", "H1_15S_9X16\n", "nonsense", None, 15])
def test_parse_version_refuses(bad):
    from media.naming import parse_version
    with pytest.raises(ValueError):
        parse_version(bad)


def test_naming_yaml_matches_the_code():
    from pathlib import Path
    import yaml
    from media.naming import HOOKS, LENGTHS
    d = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "naming.yaml").read_text())["values"]
    assert tuple(d["HOOK"]) == HOOKS
    assert tuple(d["LENGTH"]) == tuple(f"{n}S" for n in LENGTHS)
    assert {k: tuple(int(x) for x in v.split("x")) for k, v in d["FORMAT"].items()} == FORMATS
    assert d["LANG"] == ["EN"]
