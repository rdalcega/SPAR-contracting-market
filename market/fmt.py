"""Number formats and value conversions of spec/README.md, "Number formats and rounding".

The spec defines each format by Python's own behaviour (`%g`, fixed formatting, `repr`, `round`, `sum`),
so each function here is that behaviour under the spec's name. `round(x, n)` of the spec is the builtin.
Whitespace, trimming and case mapping are `str.isspace`, `str.strip`, `str.lower` and `str.upper`.
"""

from fractions import Fraction


def G(x):
    """C's %g: 6 significant digits, trailing zeros removed."""
    return "%g" % x


def F(x, n):
    """Fn: fixed n decimals, correctly rounded from the double's exact value."""
    return f"{x:.{n}f}"


def CR(x):
    """F0 for a whole number, otherwise F2."""
    return F(x, 0 if float(x).is_integer() else 2)


def CREDITS(x):
    """G and the unit; the value, not the written number, decides the singular."""
    return f"{G(x)} credit" if x == 1 else f"{G(x)} credits"


def REPR(x):
    """The shortest decimal string that reads back as the same double."""
    return repr(float(x))


def PCT(x):
    return G(round(100 * x, 6)) + "%"


def PCT0(x):
    return f"{round(100 * x)}%"


def FRAC(x):
    """The nearest fraction with a denominator of at most 10^6, in lowest terms."""
    f = Fraction(x).limit_denominator(10**6)
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def INT(n):
    return str(int(n))


def SUM(xs):
    """Compensated (Neumaier) summation in the list's order: the builtin sum over floats."""
    return sum(xs, 0.0)


def TEXT(v):
    """A value an agent sent, quoted back: a string as it is, anything else as PY."""
    return v if isinstance(v, str) else repr(v)


def PY(v):
    """A value an agent sent, as Python's repr prints it (values as read from JSON)."""
    return repr(v)


def unit(written, singular, plural=None):
    """The singular rule: the singular unit when the number is written as exactly `1`."""
    return f"{written} {singular if written == '1' else plural or singular + 's'}"
