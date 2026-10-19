#!/usr/bin/env python3
"""Point d'entrée demandé : simulation Monte Carlo de SMA92.

Exemples :
    python monte_carlo_sma92.py
    python monte_carlo_sma92.py --paths 500000 --days 60 --strikes 50,55,60
    python monte_carlo_sma92.py --model bootstrap --vol 0.55
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sma92.cli_mc import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
