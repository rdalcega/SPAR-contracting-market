"""Which firm plays which version in each episode of a generation, and which seeds a run uses.

A generation plays each of its seeds twice. On the first play, each firm uses its revision (+1) or its current
version (-1) as the table says; on the second play of the same seed, every firm switches. With 8 seeds the table is
5 columns of an 8x8 Hadamard matrix, so each firm plays its revision in exactly half the episodes and, in each firm's
comparison, the other firms' switches and their pairwise interactions cancel exactly. With another number of seeds the
table is drawn at random with each column balanced as nearly as possible (unbiased on average, not exact).
"""

import itertools
import random

FIRMS = [("buyer", 0), ("buyer", 1), ("manager", 0), ("manager", 1), ("manager", 2)]

# Seeds: training seeds start at 10,000, test seeds are 5000-5011, and 0-5 (used to tune the market) are never used.
TEST_SEEDS = list(range(5000, 5012))
TRAIN_BASE = 10_000


def hadamard(n):
    """The n x n Sylvester Hadamard matrix (n a power of 2), entries +1 and -1."""
    h = [[1]]
    while len(h) < n:
        h = [row + row for row in h] + [row + [-x for x in row] for row in h]
    if len(h) != n:
        raise ValueError(f"{n} is not a power of 2")
    return h


HADAMARD_COLUMNS = (1, 2, 3, 4, 7)  # columns of the 8x8 matrix used for the 5 firms


def table(n_seeds, rng=None):
    """n_seeds rows of +1/-1, one column per firm in FIRMS order: the first play of each seed."""
    if n_seeds == 8:
        h = hadamard(8)
        return [[h[r][c] for c in HADAMARD_COLUMNS] for r in range(8)]
    rng = rng or random.Random(0)
    cols = []
    for _ in FIRMS:
        col = [1] * (n_seeds // 2) + [-1] * (n_seeds - n_seeds // 2)
        rng.shuffle(col)
        cols.append(col)
    return [list(row) for row in zip(*cols)]


def balanced(rows):
    """Whether every column has as many +1 as -1 and every pair of columns is orthogonal (exact cancellation)."""
    cols = list(zip(*rows))
    return (all(sum(c) == 0 for c in cols)
            and all(sum(a * b for a, b in zip(x, y)) == 0 for x, y in itertools.combinations(cols, 2)))


def train_seeds(run, generation, n_seeds):
    """The seeds of one generation of one run. Arms share them, so arms are compared on the same draws."""
    if n_seeds > 100:
        raise ValueError("at most 100 seeds a generation")
    return [TRAIN_BASE * (run + 1) + 100 * generation + i for i in range(n_seeds)]


def plays(seeds, rows):
    """Every episode of a generation: (seed, play, {firm: 'revision' or 'current'}), play 0 then play 1."""
    out = []
    for play in (0, 1):
        for seed, row in zip(seeds, rows):
            sign = row if play == 0 else [-x for x in row]
            out.append((seed, play, {firm: ("revision" if s > 0 else "current") for firm, s in zip(FIRMS, sign)}))
    return out
