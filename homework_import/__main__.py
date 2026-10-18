"""Allow ``python -m homework_import ...``."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
