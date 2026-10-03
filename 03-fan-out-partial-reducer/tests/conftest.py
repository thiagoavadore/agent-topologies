import sys
from pathlib import Path

# Topology folders start with a digit, so they are script folders, not importable packages.
# Appended, not prepended: 01 has an experiment.py too, and its tests must keep importing their own.
sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))
