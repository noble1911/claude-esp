"""Entry point: ``python -m esp_gateway`` starts the WebSocket server."""

from __future__ import annotations

import asyncio
import logging

from .server import serve


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
