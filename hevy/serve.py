"""Production entry point for the web app: ``python -m hevy.serve`` (the web service)."""
import logging

import uvicorn

from .config import load_settings
from .web import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = load_settings()
    app = create_app(settings)
    # Loopback only, reachable through nginx at /hevy/. No access log (it would record what was viewed);
    # proxy_headers off so the central-auth headers are trusted from the real 127.0.0.1 peer only.
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1, access_log=False, proxy_headers=False,
                server_header=False, log_config=None, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
