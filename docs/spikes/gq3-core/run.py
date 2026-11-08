#!/usr/bin/env python3
"""Run the GQ 3 core spike: every unit test, then the stress harness.

    python3 run.py                 # tests, then 50,000 stress terms
    python3 run.py --terms 5000    # a shorter stress run
    python3 run.py --seed 1        # another stress seed

Exits non-zero when a test or a stress property fails.
"""
import argparse
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", type=int, default=50_000)
    ap.add_argument("--seed", type=int, default=20261108)
    a = ap.parse_args()
    suite = unittest.defaultTestLoader.discover(os.path.join(HERE, "tests"), top_level_dir=HERE)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    from gq3 import stress
    code = stress.main(["--terms", str(a.terms), "--seed", str(a.seed)])
    ok = result.wasSuccessful() and code == 0
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
