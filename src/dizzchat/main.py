"""Console entry point that runs the ASGI app under uvicorn."""

import uvicorn

from dizzchat.config import Settings, get_settings


def main() -> None:
    """Run the application server."""
    settings: Settings = get_settings()
    uvicorn.run(
        "dizzchat.app:app",
        host=settings.host,
        port=settings.port,
        log_config=None,  # keep our JSON logging, don't let uvicorn override it
    )


if __name__ == "__main__":
    main()
