"""Resolve the repository's scripts consistently for discovery and individual suites."""

from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY / "scripts"))
