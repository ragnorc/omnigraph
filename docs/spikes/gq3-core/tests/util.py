import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gq3 import core as C            # noqa: E402
from gq3.core import Config, PROPOSED, RFC_LITERAL   # noqa: E402
from gq3.model import GQError        # noqa: E402
from gq3.fixtures import standard_graph   # noqa: E402


def ids(rel, col=0):
    return [r[col].id if r[col] is not None else None for r in rel.rows]


def sids(rel, col=0):
    return sorted(ids(rel, col), key=lambda x: (x is None, x))


def bag(rel):
    from collections import Counter
    return Counter(rel.rows)
