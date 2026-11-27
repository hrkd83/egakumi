"""Run one of the two long-lived Container Apps from a shared image."""
import logging
import sys

import uvicorn


def main():
    logging.getLogger('azure').setLevel(logging.CRITICAL)
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    if sys.argv[1:] == ['worker']:
        from .worker import create_worker_app
        app = create_worker_app()
    elif not sys.argv[1:]:
        from .relay import create_relay_app
        app = create_relay_app()
    else:
        raise SystemExit('Use either the relay or worker command')
    uvicorn.run(app, host='0.0.0.0', port=8000, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
