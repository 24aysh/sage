"""Support python -m sage.cli as well as the installed sage command."""

from sage.cli import main

raise SystemExit(main())
