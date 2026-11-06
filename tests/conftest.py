"""I moduli del servizio stanno nella root del repository, non in un pacchetto."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
