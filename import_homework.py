#!/usr/bin/env python3
"""Compatibility wrapper for ``python import_homework.py ...``.

The implementation lives in :mod:`homework_import.cli`.
"""
from homework_import.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
