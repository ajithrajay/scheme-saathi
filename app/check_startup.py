"""Check application creation and lifespan without touching the user's data."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from .config import Config
from .main import create_app


async def check_startup():
    with TemporaryDirectory(prefix="schemesaathi-startup-") as directory:
        app = create_app(Config(data_dir=Path(directory), headless=True))
        async with app.router.lifespan_context(app):
            if not app.state.store.all("scheme"):
                raise RuntimeError("The bundled scheme catalogue is empty.")


if __name__ == "__main__":
    asyncio.run(check_startup())
    print("Application startup and catalogue check passed.")
