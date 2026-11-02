#!/usr/bin/env python3
"""Generate introduction audio through the unchanged shared Gemini generator."""
import sys
from intro_runtime import enable_intro_identity, load_tool


if __name__ == '__main__':
    try:
        shared = load_tool('generate-gemini-lesson.py')
        enable_intro_identity(shared)
        sys.exit(shared.main())
    except (ValueError, KeyError, FileExistsError, FileNotFoundError) as error:
        print('Stopped: ' + str(error) if isinstance(error, ValueError)
              else f'Stopped: {type(error).__name__}. No automatic request retry.', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Interrupted. In-flight usage is unknown; no automatic resend.', file=sys.stderr)
        sys.exit(130)
