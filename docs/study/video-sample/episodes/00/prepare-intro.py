#!/usr/bin/env python3
"""Prepare introduction requests with the shared cast, provenance and checks."""
import sys
from intro_runtime import enable_intro_identity, load_tool


if __name__ == '__main__':
    try:
        shared = load_tool('prepare-gemini-lesson.py')
        enable_intro_identity(shared.production)
        sys.exit(shared.main())
    except (ValueError, KeyError, FileNotFoundError, TypeError) as error:
        print('Stopped: ' + str(error) if isinstance(error, ValueError)
              else f'Stopped: {type(error).__name__}. No network request was made.', file=sys.stderr)
        sys.exit(1)
