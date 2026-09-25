import sys
from pathlib import Path

# Topology folders start with a digit, so they are script folders, not importable packages.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
