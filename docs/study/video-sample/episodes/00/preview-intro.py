#!/usr/bin/env python3
"""Make a continuous introduction preview with all shared clip checks."""
import signal
from intro_runtime import intro_episode_key, load_tool


if __name__ == '__main__':
    shared = load_tool('make-conversation-preview.py')
    shared.episode_key = intro_episode_key
    signal.signal(signal.SIGTERM, shared.interrupted)
    try:
        shared.main()
    except (ValueError, KeyError, OSError, StopIteration, shared.json.JSONDecodeError) as error:
        raise SystemExit('Stopped: ' + str(error)) from None
    except KeyboardInterrupt:
        raise SystemExit('Stopped: preview interrupted; existing completed files were preserved.') from None
