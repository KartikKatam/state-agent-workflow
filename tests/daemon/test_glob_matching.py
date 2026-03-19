"""Tests for path_matches_glob utility."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.workflow_state import path_matches_glob


class TestPathMatchesGlob:
    def test_simple_match(self):
        assert path_matches_glob("tests/test_foo.py", "tests/*.py")

    def test_simple_no_match(self):
        assert not path_matches_glob("src/foo.py", "tests/*.py")

    def test_recursive_glob(self):
        assert path_matches_glob("tests/sub/deep/test_foo.py", "tests/**/*.py")

    def test_recursive_glob_direct(self):
        assert path_matches_glob("tests/test_foo.py", "tests/**/*.py")

    def test_leading_dot_slash(self):
        assert path_matches_glob("./tests/test_foo.py", "tests/*.py")
        assert path_matches_glob("tests/test_foo.py", "./tests/*.py")

    def test_question_mark(self):
        assert path_matches_glob("src/a.py", "src/?.py")
        assert not path_matches_glob("src/ab.py", "src/?.py")

    def test_no_slash_crossing_single_star(self):
        assert not path_matches_glob("tests/sub/test_foo.py", "tests/*.py")

    def test_double_star_at_start(self):
        assert path_matches_glob("deeply/nested/conftest.py", "**/conftest.py")

    def test_exact_match(self):
        assert path_matches_glob("src/main.py", "src/main.py")

    def test_handoff_glob(self):
        assert path_matches_glob(".claude/handoffs/summary.md", ".claude/handoffs/**")
