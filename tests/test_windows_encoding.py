"""Exercise startup with the Windows cp1252 default, even on Linux."""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import __version__
from app.check_startup import check_startup
from app.config import Config, ROOT
from app.main import create_app


_path_open = Path.open


def windows_text_open(self, mode="r", buffering=-1, encoding=None,
                      errors=None, newline=None):
    if "b" not in mode and encoding in (None, "locale"):
        encoding = "cp1252"
    return _path_open(self, mode, buffering, encoding, errors, newline)


class WindowsEncodingTests(unittest.TestCase):
    def test_startup_and_restart_preserve_tamil_with_cp1252_default(self):
        with TemporaryDirectory() as directory, patch.object(Path, "open", windows_text_open):
            # Confirm this environment reproduces the original Windows failure.
            with self.assertRaises(UnicodeDecodeError):
                (ROOT / "app/data/catalogue.json").read_text()

            config = Config(data_dir=Path(directory), token="encoding-test", headless=True)
            for launch in range(2):
                with self.subTest(launch=launch), TestClient(create_app(config)) as client:
                    self.assertEqual(client.get("/").status_code, 200)
                    response = client.post("/api/bootstrap", json={"token": config.token})
                    self.assertEqual(response.status_code, 200)
                    health = client.get("/api/health")
                    self.assertEqual(health.status_code, 200)
                    self.assertEqual(health.json()["version"], __version__)
                    schemes = client.get("/api/schemes")
                    self.assertEqual(schemes.status_code, 200)
                    catalogue = {scheme["id"]: scheme for scheme in schemes.json()}
                    self.assertEqual(catalogue["pudhumai-penn"]["name_ta"], "புதுமைப் பெண்")
                    if launch == 0:
                        response = client.put(
                            "/api/profile", json={"facts": {"full_name": "கவ்யா"}},
                            headers={"X-Saathi-Request": "1"},
                        )
                        self.assertEqual(response.status_code, 200)
                    profile = client.get("/api/profile")
                    self.assertEqual(profile.status_code, 200)
                    self.assertEqual(profile.json()["facts"]["full_name"], "கவ்யா")

    def test_installer_startup_check_with_cp1252_default(self):
        with patch.object(Path, "open", windows_text_open):
            asyncio.run(check_startup())


if __name__ == "__main__":
    unittest.main()
