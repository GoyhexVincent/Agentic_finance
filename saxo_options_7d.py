#!/usr/bin/env python3
"""Point d'entrée demandé : options SMA92 sur Saxo et historique 7 jours.

Sans jeton, tester le pipeline :
    python saxo_options_7d.py --dry-run

Avec un jeton 24 h Saxo SIM dans .env :
    python saxo_options_7d.py --symbol SMA92 --days 7
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sma92.cli_saxo import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
