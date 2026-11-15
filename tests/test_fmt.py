"""Every example of spec/README.md, "Number formats and rounding"."""

import json
import math

from market.fmt import CR, CREDITS, FRAC, G, INT, PCT, PCT0, PY, REPR, SUM, TEXT, F, unit


def test_G():
    assert [G(x) for x in (20.0, 20.25, 0.15, 1.875)] == ["20", "20.25", "0.15", "1.875"]
    assert G(1e6) == "1e+06" and G(1234567.0) == "1.23457e+06"
    assert G(-0.0) == "-0"
    assert G(0.0001) == "0.0001" and G(0.00001) == "1e-05"


def test_CR():
    assert [CR(x) for x in (6.0, 1.8, 5.55, 1234567.0)] == ["6", "1.80", "5.55", "1234567"]
    assert CR(1e22) == "10000000000000000000000"


def test_CREDITS():
    assert CREDITS(1.0) == "1 credit" and CREDITS(2.8) == "2.8 credits"
    assert CREDITS(0.9999999) == "1 credits"


def test_F():
    assert F(81.6, 2) == "81.60" and F(5.7, 1) == "5.7"
    assert F(-0.001, 2) == "-0.00" and F(-0.0, 2) == "-0.00"
    assert F(0.125, 2) == "0.12" and F(2.5, 0) == "2"  # ties to even on the exact value


def test_REPR():
    assert REPR(25.0) == "25.0" and REPR(24.55) == "24.55" and REPR(1.0) == "1.0"
    assert REPR(1e16) == "1e+16" and REPR(1e-5) == "1e-05"
    assert REPR(-0.0) == "-0.0" and REPR(math.nan) == "nan"
    assert REPR(math.inf) == "inf" and REPR(-math.inf) == "-inf"
    assert REPR(25) == "25.0"


def test_PCT():
    assert PCT(0.2) == "20%" and PCT(0.125) == "12.5%"
    assert PCT0(0.2) == "20%" and PCT0(0.125) == "12%"


def test_FRAC():
    assert FRAC(0.5) == "1/2" and FRAC(3 / 16) == "3/16" and FRAC(2.0) == "2"
    assert FRAC(1 / 3) == "1/3"


def test_INT():
    assert INT(4) == "4"


def test_round_and_SUM():
    assert round(2.675, 2) == 2.67  # the double is just below 2.675
    assert SUM([6.2, 2.7, 6.8, 4.6]) == 20.3
    assert 6.2 + 2.7 + 6.8 + 4.6 == 20.299999999999997
    assert SUM([]) == 0.0


def test_TEXT_and_PY():
    assert TEXT("contract 1") == "contract 1" and TEXT(3) == "3" and TEXT(3.0) == "3.0"
    assert TEXT(True) == "True" and TEXT(False) == "False" and TEXT(None) == "None"
    assert TEXT(json.loads("-0")) == "0" and TEXT(-0.0) == "-0.0"
    assert TEXT(json.loads("123456789012345678901234567890")) == "123456789012345678901234567890"
    assert [TEXT(x) for x in (math.nan, math.inf, -math.inf)] == ["nan", "inf", "-inf"]
    assert TEXT([7]) == "[7]"
    assert PY("7.5") == "'7.5'" and PY(True) == "True" and PY([7]) == "[7]"
    assert PY(json.loads("[null, NaN]")) == "[None, nan]"
    assert PY("a'b") == '"a\'b"'
    assert PY("a'\"b") == "'a\\'\"b'"
    assert PY("x\u00a0\u200b") == "'x\\xa0\\u200b'"
    assert PY("a\\b\n\r\t") == "'a\\\\b\\n\\r\\t'"
    assert PY("\U000e0001") == "'\\U000e0001'" and PY("\ud800") == "'\\ud800'"
    assert PY("\u00e9\U0001f600") == "'\u00e9\U0001f600'"
    assert PY({"a": 1, "b": [None, "c"]}) == "{'a': 1, 'b': [None, 'c']}"


def test_whitespace_and_case():
    spaces = [0x9, 0xA, 0xB, 0xC, 0xD, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x85, 0xA0, 0x1680,
              *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000]
    assert [c for c in range(0x110000) if chr(c).isspace()] == spaces
    assert "\ufb00".upper() == "FF"


def test_unit():
    assert unit("1", "day") == "1 day" and unit("2", "day") == "2 days"
    assert unit("1.00", "credit") == "1.00 credits"
    assert unit("1", "run of its own", "runs of its own") == "1 run of its own"
