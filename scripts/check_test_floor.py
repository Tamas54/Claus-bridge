#!/usr/bin/env python3
"""Run the test suite and refuse to report success on a suspiciously small run.

THE FAILURE MODE THIS EXISTS FOR
--------------------------------
"All tests passed" is only half a claim. It means everything that ran passed.
It says nothing about how much ran. A test file that dies during import, a
directory that drops out of `testpaths`, a rename that stops matching
`test_*.py` — in each case the surviving files all pass and the run goes
green with a chunk of the suite silently missing.

This wrapper adds the other half: "and roughly everything we expected to run,
ran." It executes pytest ONCE (no double-running the suite for a count),
reads the machine-readable JUnit XML pytest already knows how to emit, and
fails if the number of registered tests falls below the floor — or if the
number cannot be determined at all, because an uncountable run is exactly the
shape a collection failure has.

USAGE
    python scripts/check_test_floor.py            # full suite + floor (CI)
    python scripts/check_test_floor.py tests/x.py # targeted, floor skipped
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

# The FLOOR, not an exact count. An exact count would catch the same failure
# mode, but it would need editing on every commit that adds a test. This sits
# a little below the last verified full run so that adding tests never touches
# it, while losing a meaningful slice of the suite does.
#
# Last verified full run: see VERIFIED_COUNT. If the suite legitimately
# shrinks below the floor, lower this number in the same commit that removes
# the tests, so it is a visible decision and not a silent one.
TEST_COUNT_FLOOR = 600
VERIFIED_COUNT = 820  # 2026-08-31, Python 3.13.11, `-m "not network"`.
# Raised from 480/501. The old numbers were measured on 2026-08-29 against a
# clean checkout while four test files (test_echolot_redirect_follow,
# test_error_surface, test_presence, test_recipe_health) were still
# uncommitted; that note asked whoever committed them to raise the floor, and
# nobody did. Re-measured on a clean `git archive HEAD` checkout: 618
# collected -- so the 480 floor was letting 138 tests disappear in silence,
# which is exactly the failure this file exists to catch. 648 = 618 on HEAD
# plus the 30 new tests landing in this change (S-009 engine selection,
# Feldwebel wiring, anonymous-call counting).

# The canonical full-suite invocation. `-m "not network"` deselects tests that
# need live services and real credentials; they show up as "deselected" in the
# run output rather than passing quietly without the keys they require.
DEFAULT_ARGS = ["-m", "not network"]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def count_registered_tests(junit_xml_path: str):
    """Registered tests from pytest's JUnit XML, or None if uncountable.

    Registered rather than executed on purpose: a skipped test still proves
    its file was imported and collected, which is precisely the thing that
    silently disappears in the failure mode above.
    """
    try:
        root = ET.parse(junit_xml_path).getroot()
    except (OSError, ET.ParseError):
        return None

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = 0
    found = False
    for suite in suites:
        raw = suite.get("tests")
        if raw is None:
            continue
        try:
            total += int(raw)
            found = True
        except ValueError:
            return None
    return total if found else None


def evaluate(exit_code, counted, floor):
    """Pure decision function, kept separate so it is testable."""
    if counted is None:
        return False, (
            "test-floor: could not determine how many tests ran (no usable "
            "JUnit XML). Refusing to report a pass on a run that cannot be "
            "counted — that is what a collection failure looks like."
        )
    if exit_code != 0:
        return False, (
            "test-floor: pytest exited with code %s (%d tests counted). "
            "See the report above." % (exit_code, counted)
        )
    if floor is not None and counted < floor:
        return False, (
            "test-floor: only %d tests were counted, below the floor of %d.\n"
            "Every test that ran passed — but part of the suite did not run at "
            "all. Likely causes: a test file failing at import time, or "
            "testpaths/norecursedirs in pytest.ini no longer matching it.\n"
            "If the suite genuinely shrank, lower TEST_COUNT_FLOOR in "
            "scripts/check_test_floor.py in the same commit." % (counted, floor)
        )
    if floor is None:
        return True, "test-floor: %d tests counted (targeted run, floor skipped) OK" % counted
    return True, "test-floor: %d tests counted, floor is %d OK" % (counted, floor)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    targeted = bool(argv)  # any explicit argument means a deliberate subset
    args = argv if targeted else list(DEFAULT_ARGS)

    scratch = tempfile.mkdtemp(prefix="claus-bridge-test-floor-")
    junit = os.path.join(scratch, "junit.xml")
    try:
        # -o junit_family=xunit2 keeps the `tests=` attribute stable across
        # pytest versions. stdio is inherited so a human still watches the
        # normal live report; the count comes from the file, which cannot be
        # corrupted by interleaved test output the way a pipe can.
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "--junitxml=" + junit,
             "-o", "junit_family=xunit2", *args],
            cwd=REPO_ROOT,
        )
        counted = count_registered_tests(junit)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    ok, message = evaluate(completed.returncode, counted,
                           None if targeted else TEST_COUNT_FLOOR)
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else (completed.returncode or 1)


if __name__ == "__main__":
    raise SystemExit(main())
