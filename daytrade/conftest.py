"""Pytest bootstrap, scoped to daytrade/ so the flat-package names (regime,
broker, bars...) never shadow anything for future tests elsewhere in the repo.
Modules here import each other by bare name, so tests need this dir on sys.path."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
