"""Pytest bootstrap. daytrade/ is a flat package whose modules import each other
by bare name (`from stockfish_exit import ...`), so collection from the repo
root needs daytrade/ on sys.path. Nothing else belongs here."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "daytrade"))
