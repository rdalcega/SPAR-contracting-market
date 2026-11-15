"""Blind tests derived solely from spec/README.md, "Number formats and rounding".

Written by an independent GPT agent that never saw market/fmt.py. Characters that were damaged in
transport (raw control and non-ASCII characters) are restored here as escapes, following each case's id.
"""

import math

import pytest

from market.fmt import (
    G, F, CR, CREDITS, REPR, PCT, PCT0, FRAC, INT, SUM, TEXT, PY, unit,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        (20, "20"),
        (20.25, "20.25"),
        (0.15, "0.15"),
        (1.875, "1.875"),
        (0.0, "0"),
        (-0.0, "-0"),
        (1.23000, "1.23"),
        (1234567.0, "1.23457e+06"),
        (123456.5, "123456"),
        (123457.5, "123458"),
        (-123456.5, "-123456"),
        (0.0001, "0.0001"),
        (0.00001, "1e-05"),
        (-0.00001, "-1e-05"),
        (999999.0, "999999"),
        (1000000.0, "1e+06"),
        (999999.5, "1e+06"),
        (0.00009999999, "0.0001"),
        (1e100, "1e+100"),
    ],
)
def test_g(value, expected):
    # "C's %g: 6 significant digits," with stripped zeros and exponent thresholds.
    assert G(value) == expected


@pytest.mark.parametrize(
    "value, places, expected",
    [
        (81.6, 2, "81.60"),
        (5.7, 1, "5.7"),
        (0.0, 0, "0"),
        (0.0, 4, "0.0000"),
        (-0.0, 0, "-0"),
        (-0.0, 2, "-0.00"),
        (-0.0, 4, "-0.0000"),
        (-0.001, 2, "-0.00"),
        (-0.49, 0, "-0"),
        (0.001, 2, "0.00"),
        (2.5, 0, "2"),
        (3.5, 0, "4"),
        (-2.5, 0, "-2"),
        (-3.5, 0, "-4"),
        (0.5, 0, "0"),
        (-0.5, 0, "-0"),
        (1.25, 1, "1.2"),
        (1.75, 1, "1.8"),
        (-1.25, 1, "-1.2"),
        (0.125, 2, "0.12"),
        (0.375, 2, "0.38"),
        (-0.125, 2, "-0.12"),
        (-0.375, 2, "-0.38"),
        (2.675, 2, "2.67"),
        (1.005, 2, "1.00"),
        (1 / 128, 6, "0.007812"),
        (3 / 128, 6, "0.023438"),
        (1 / 8, 10, "0.1250000000"),
        (1234567.0, 2, "1234567.00"),
        (float(2**60), 2, "1152921504606846976.00"),
    ],
)
def test_fixed(value, places, expected):
    # "Fixed n decimals, correctly rounded from the double's exact value (ties to even)."
    assert F(value, places) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (6.0, "6"),
        (1.8, "1.80"),
        (5.55, "5.55"),
        (1234567.0, "1234567"),
        (0.0, "0"),
        (-0.0, "-0"),
        (-6.0, "-6"),
        (-1.8, "-1.80"),
        (0.125, "0.12"),
        (0.375, "0.38"),
        (-0.001, "-0.00"),
        (0.999, "1.00"),
        (float(2**60), "1152921504606846976"),
    ],
)
def test_cr(value, expected):
    # "F0 if the value is a whole number ... otherwise F2."
    assert CR(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (1, "1 credit"),
        (1.0, "1 credit"),
        (2.8, "2.8 credits"),
        (0.9999999, "1 credits"),
        (1.0000001, "1 credits"),
        (0.0, "0 credits"),
        (-0.0, "-0 credits"),
        (-1.0, "-1 credits"),
        (1000000.0, "1e+06 credits"),
    ],
)
def test_credits(value, expected):
    # "The value decides, not the written G."
    assert CREDITS(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (25.0, "25.0"),
        (24.55, "24.55"),
        (1.0, "1.0"),
        (25, "25.0"),
        (0, "0.0"),
        (0.0, "0.0"),
        (-0.0, "-0.0"),
        (0.0001, "0.0001"),
        (0.00001, "1e-05"),
        (-0.00001, "-1e-05"),
        (1e15, "1000000000000000.0"),
        (1e16, "1e+16"),
        (-1e16, "-1e+16"),
        (1e100, "1e+100"),
        (1.0000000000000002, "1.0000000000000002"),
        (9007199254740993, "9007199254740992.0"),
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (-float("inf"), "-inf"),
    ],
)
def test_repr(value, expected):
    # "The shortest decimal string that reads back as the same double"; Python float repr.
    assert REPR(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.2, "20%"),
        (0.125, "12.5%"),
        (1.0, "100%"),
        (0.0, "0%"),
        (-0.0, "-0%"),
        (-0.125, "-12.5%"),
        (1 / 512, "0.195312%"),
        (3 / 512, "0.585938%"),
        (-1 / 512, "-0.195312%"),
        (0.000000004, "0%"),
        (-0.000000004, "-0%"),
        (0.000001, "0.0001%"),
        (10000.0, "1e+06%"),
    ],
)
def test_pct(value, expected):
    # "G of round(100 x x, 6), followed by %"; round uses exact-double ties to even.
    assert PCT(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.2, "20%"),
        (0.125, "12%"),
        (0.375, "38%"),
        (-0.125, "-12%"),
        (-0.375, "-38%"),
        (0.0, "0%"),
        (-0.0, "0%"),
        (0.001, "0%"),
        (-0.001, "0%"),
        (1.0, "100%"),
        (10000.0, "1000000%"),
    ],
)
def test_pct0(value, expected):
    # "The integer nearest to the double 100 x x, ties to even."
    assert PCT0(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.5, "1/2"),
        (0.1875, "3/16"),
        (-0.1875, "-3/16"),
        (0.1, "1/10"),
        (1.25, "5/4"),
        (-1.25, "-5/4"),
        (0.0, "0"),
        (-0.0, "0"),
        (1.0, "1"),
        (-2.0, "-2"),
        (0.0000001, "0"),
        (-0.0000001, "0"),
        (0.000001, "1/1000000"),
        (1 / 1048576, "1/1000000"),
        (-1 / 1048576, "-1/1000000"),
        (1.0000001, "1"),
        (float(2**60), "1152921504606846976"),
    ],
)
def test_frac(value, expected):
    # "The fraction nearest to x with a denominator of at most 10^6," in lowest terms.
    assert FRAC(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (4, "4"),
        (0, "0"),
        (-4, "-4"),
        (1234567, "1234567"),
    ],
)
def test_int(value, expected):
    # "INT: A plain integer."
    assert INT(value) == expected


@pytest.mark.parametrize(
    "values, expected",
    [
        ([], 0.0),
        ([1.5], 1.5),
        ([6.2, 2.7, 6.8, 4.6], 20.3),
        ([1e16, 1.0, -1e16], 1.0),
        ([1.0, 1e16, -1e16], 1.0),
        ([1e16, 1.0, 1.0, -1e16], 2.0),
        ([-1e16, -1.0, 1e16], -1.0),
        ([1.0, -1.0], 0.0),
        ([1e308, -1e308, 1e308, -1e308], 0.0),
    ],
)
def test_sum_finite(values, expected):
    # "Compensated (Neumaier) summation in the list's order, in double arithmetic."
    result = SUM(values)
    assert isinstance(result, float)
    assert result == expected


@pytest.mark.parametrize("values", [[], [-0.0], [-0.0, -0.0]])
def test_sum_zero_sign(values):
    # "Start with hi = 0 and lo = 0"; return hi when lo is zero.
    result = SUM(values)
    assert isinstance(result, float)
    assert result == 0.0
    assert math.copysign(1.0, result) == 1.0


@pytest.mark.parametrize(
    "values, expected",
    [
        ([float("inf")], float("inf")),
        ([-float("inf")], -float("inf")),
        ([1.0, float("inf"), 2.0], float("inf")),
        ([float("inf"), float("inf")], float("inf")),
        ([1e308, 1e308, -1e308, -1e308], float("inf")),
        ([-1e308, -1e308, 1e308, 1e308], -float("inf")),
    ],
)
def test_sum_infinite_or_overflowed(values, expected):
    # "The sum is hi + lo if lo is non-zero and finite, else hi."
    result = SUM(values)
    assert isinstance(result, float)
    assert result == expected


@pytest.mark.parametrize(
    "values",
    [
        [float("nan")],
        [1.0, float("nan"), 2.0],
        [float("inf"), -float("inf")],
        [-float("inf"), float("inf")],
    ],
)
def test_sum_nan(values):
    # The stated SUM recurrence uses double arithmetic and returns hi for non-finite lo.
    result = SUM(values)
    assert isinstance(result, float)
    assert math.isnan(result)


@pytest.mark.parametrize("convert", [TEXT, PY], ids=["TEXT", "PY"])
@pytest.mark.parametrize(
    "value, expected",
    [
        (3, "3"),
        (-0, "0"),
        (-17, "-17"),
        (3.0, "3.0"),
        (0.0, "0.0"),
        (-0.0, "-0.0"),
        (0.00001, "1e-05"),
        (1e16, "1e+16"),
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (-float("inf"), "-inf"),
        (True, "True"),
        (False, "False"),
        (None, "None"),
        (9007199254740993, "9007199254740993"),
        (123456789012345678901234567890, "123456789012345678901234567890"),
        (-123456789012345678901234567890, "-123456789012345678901234567890"),
        (10**400, "1" + "0" * 400),
        (-(10**400), "-1" + "0" * 400),
        (10**4299, "1" + "0" * 4299),
    ],
)
def test_text_and_py_scalar_values(convert, value, expected):
    # TEXT specifies exact integer digits, REPR doubles, True/False/None; PY uses the same.
    assert convert(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "contract 1",
        "7.5",
        "a'b",
        'a"b',
        "a'\"b",
        "\\\n\r\t",
        "\x00\x85\xa0\u200b\u2028\u2029",
        "\ud800\U000f0000\U0010ffff",
        "\u00e9\U0001f600",
    ],
)
def test_text_preserves_strings(value):
    # "TEXT(v): a string as it is."
    assert TEXT(value) == value


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", "''"),
        ("7.5", "'7.5'"),
        ("contract 1", "'contract 1'"),
        ("a'b", "\"a'b\""),
        ("'", "\"'\""),
        ('a"b', "'a\"b'"),
        ("a'\"b", "'a\\'\"b'"),
        ("\\", r"'\\'"),
        ("a\\b", r"'a\\b'"),
        ("a'b\\c", "\"a'b\\\\c\""),
        ("\n", r"'\n'"),
        ("\r", r"'\r'"),
        ("\t", r"'\t'"),
        ("\n\r\t", r"'\n\r\t'"),
        ("x\xa0\u200b", r"'x\xa0\u200b'"),
        ("\u00e9\U0001f600", "'\u00e9\U0001f600'"),
        (" ", "' '"),
        ("\u00e1", "'\u00e1'"),
        ("\ufb00", "'\ufb00'"),
    ],
)
def test_py_string_quotes_and_special_escapes(value, expected):
    # PY specifies quote selection, backslash/newline/CR/tab escapes, and printable Unicode.
    assert PY(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        pytest.param("\x00", r"'\x00'", id="Cc-null"),
        pytest.param("\x08", r"'\x08'", id="Cc-backspace"),
        pytest.param("\x0b", r"'\x0b'", id="Cc-vertical-tab"),
        pytest.param("\x0c", r"'\x0c'", id="Cc-form-feed"),
        pytest.param("\x1f", r"'\x1f'", id="Cc-unit-separator"),
        pytest.param("\x7f", r"'\x7f'", id="Cc-delete"),
        pytest.param("\x85", r"'\x85'", id="Cc-next-line"),
        pytest.param("\xad", r"'\xad'", id="Cf-short-escape"),
        pytest.param("\u200b", r"'\u200b'", id="Cf-four-digits"),
        pytest.param("\U000e0001", r"'\U000e0001'", id="Cf-eight-digits"),
        pytest.param("\ud800", r"'\ud800'", id="Cs-high-surrogate"),
        pytest.param("\udfff", r"'\udfff'", id="Cs-low-surrogate"),
        pytest.param("\ue000", r"'\ue000'", id="Co-BMP"),
        pytest.param("\U000f0000", r"'\U000f0000'", id="Co-plane-15"),
        pytest.param("\U00100000", r"'\U00100000'", id="Co-plane-16"),
        pytest.param("\u0378", r"'\u0378'", id="Cn-unassigned"),
        pytest.param("\U0010ffff", r"'\U0010ffff'", id="Cn-noncharacter"),
        pytest.param("\u2028", r"'\u2028'", id="Zl"),
        pytest.param("\u2029", r"'\u2029'", id="Zp"),
        pytest.param("\xa0", r"'\xa0'", id="Zs-below-100"),
        pytest.param("\u1680", r"'\u1680'", id="Zs-four-digits"),
        pytest.param("\u2000", r"'\u2000'", id="Zs-en-quad"),
        pytest.param("\u202f", r"'\u202f'", id="Zs-narrow-no-break"),
        pytest.param("\u205f", r"'\u205f'", id="Zs-medium-math"),
        pytest.param("\u3000", r"'\u3000'", id="Zs-ideographic"),
    ],
)
def test_py_nonprintable_categories(value, expected):
    # PY names Cc/Cf/Cs/Co/Cn/Zl/Zp/Zs and requires lowercase, width-specific hex escapes.
    assert PY(value) == expected


@pytest.mark.parametrize("convert", [TEXT, PY], ids=["TEXT", "PY"])
@pytest.mark.parametrize(
    "value, expected",
    [
        ([], "[]"),
        ({}, "{}"),
        ([7], "[7]"),
        ([None, float("nan")], "[None, nan]"),
        ([True, False, None], "[True, False, None]"),
        ([3, 3.0, -0.0], "[3, 3.0, -0.0]"),
        ([float("inf"), -float("inf")], "[inf, -inf]"),
        (["7.5", "a'b", "a'\"b"], "['7.5', \"a'b\", 'a\\'\"b']"),
        ({"a": 1, "b": 2}, "{'a': 1, 'b': 2}"),
        ({"b": 2, "a": 1}, "{'b': 2, 'a': 1}"),
        ({"a'b": "\n"}, "{\"a'b\": '\\n'}"),
        ({"x\xa0": "\u200b"}, r"{'x\xa0': '\u200b'}"),
        (
            {"outer": [7, {"inner": [None, float("nan"), "\u00e9\U0001f600"]}], "empty": {}},
            "{'outer': [7, {'inner': [None, nan, '\u00e9\U0001f600']}], 'empty': {}}",
        ),
        (
            [{"n": 123456789012345678901234567890}, [[], {}]],
            "[{'n': 123456789012345678901234567890}, [[], {}]]",
        ),
    ],
)
def test_text_and_py_containers(convert, value, expected):
    # TEXT lists/objects use PY; PY recursively joins items/pairs with the specified punctuation.
    assert convert(value) == expected


@pytest.mark.parametrize(
    "singular",
    ["day", "second", "character", "action", "slot", "run", "round", "credit"],
)
def test_unit_singular_examples(singular):
    # "Takes the singular unit when it is written as exactly 1," followed by these examples.
    assert unit("1", singular) == "1 " + singular


@pytest.mark.parametrize(
    "written, expected",
    [
        ("0", "0 credits"),
        ("2", "2 credits"),
        ("1.00", "1.00 credits"),
        ("1.0", "1.0 credits"),
        ("01", "01 credits"),
        ("+1", "+1 credits"),
        ("-1", "-1 credits"),
        ("1e0", "1e0 credits"),
        (" 1", " 1 credits"),
        ("1 ", "1  credits"),
        ("-0", "-0 credits"),
        ("nan", "nan credits"),
    ],
)
def test_unit_requires_exact_written_one(written, expected):
    # "Written as exactly 1"; "a cent amount written 1.00 keeps the plural."
    assert unit(written, "credit") == expected


@pytest.mark.parametrize(
    "written, expected",
    [("1", "1 person"), ("2", "2 people"), ("1.00", "1.00 people")],
)
def test_unit_explicit_plural(written, expected):
    # The singular rule selects by written text; the supplied API permits an explicit plural.
    assert unit(written, "person", "people") == expected


def test_unit_and_credits_use_different_singular_rules():
    # "0.9999999 ... reads 1 credits ... while a unit field ... reads 1 credit."
    assert G(0.9999999) == "1"
    assert CREDITS(0.9999999) == "1 credits"
    assert unit("1", "credit") == "1 credit"
