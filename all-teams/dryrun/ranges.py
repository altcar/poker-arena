"""Starting-hand rankings built from the preflop equity table."""

import fasteval as fe
from preflop_table import EQ

SIZES = fe.CLASS_SIZE


def _vs_random():
    out = []
    total = sum(SIZES)
    for i in range(169):
        s = 0
        for j in range(169):
            s += EQ[i * 169 + j] * SIZES[j]
        out.append(s / total / 10000)
    return out


# Equity of each class against a random hand.
VS_RANDOM = _vs_random()
# Classes from strongest to weakest by equity against a random hand.
ORDER = sorted(range(169), key=lambda i: -VS_RANDOM[i])


def _percentiles():
    """Per class: the share of all combos ranked at or above it (0..1),
    taking the middle of the class's own block."""
    out = [0.0] * 169
    done = 0
    for i in ORDER:
        out[i] = (done + SIZES[i] / 2) / 1326
        done += SIZES[i]
    return out


# 0.0 = best hand (AA), 1.0 = worst (32o).
PERCENTILE = _percentiles()


def in_top(cls, fraction):
    return PERCENTILE[cls] <= fraction


def eq(i, j):
    return EQ[i * 169 + j] / 10000
