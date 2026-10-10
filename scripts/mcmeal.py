"""Portable offline entrypoint used by the McMealMate Skill."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcmeal.native import main

if __name__ == '__main__':
    main()
