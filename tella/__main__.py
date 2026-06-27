"""``python -m tella`` → CLI entry."""
from __future__ import annotations

import sys

from tella.cli import main


if __name__ == "__main__":
    sys.exit(main())
