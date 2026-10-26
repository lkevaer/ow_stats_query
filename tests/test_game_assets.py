from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import re
import unittest
from xml.etree import ElementTree

from fastapi.testclient import TestClient
from PIL import Image
from api_server.main import app


class PackagedGameAssetsTests(unittest.TestCase):
    def test_all_catalog_images_and_fallbacks_are_served_locally_and_decode(self):
        static = Path(__file__).resolve().parents[1] / "api_server/static"
        script = (static / "game_assets.js").read_text(encoding="utf-8")
        catalog = json.loads(re.search(r"/\* ASSET_CATALOG_START \*/(.*?)/\* ASSET_CATALOG_END \*/", script, re.S)[1])
        paths = {row["path"] for rows in catalog.values() for row in rows}
        self.assertEqual(set(catalog), {"heroes", "maps", "ranks"})
        self.assertGreaterEqual(len(catalog["heroes"]), 54)
        with closing(TestClient(app)) as client:
            for path in paths:
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    if path.endswith(".webp"):
                        self.assertEqual(response.headers["content-type"], "image/webp")
                        with Image.open(BytesIO(response.content)) as image:
                            self.assertEqual(image.format, "WEBP")
                            self.assertLessEqual(image.width, 400)
                            self.assertLessEqual(image.height, 256)
                            image.verify()
                    else:
                        ElementTree.fromstring(response.content)
            for kind in ("hero", "map", "rank"):
                response = client.get(f"/static/game-icons/{kind}-default.svg")
                self.assertEqual(response.status_code, 200)
                ElementTree.fromstring(response.content)
