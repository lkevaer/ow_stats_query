"""Check the actual deployable frontend, including files inside the Docker image."""
import base64
from contextlib import closing
import hashlib
from html.parser import HTMLParser
from pathlib import Path
from io import BytesIO
import tempfile
import unittest
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from PIL import Image

from api_server.frontend import STATIC_DIR, rating_page
from api_server.main import app


class Assets(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.assets = []
        self.meta = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("property"):
            self.meta[attrs["property"]] = attrs.get("content", "")
        url = attrs.get("src") if tag == "script" else attrs.get("href") if tag == "link" else None
        if url and url.startswith("/static/"):
            self.assets.append((url, attrs.get("integrity")))


class FrontendReleaseTests(unittest.TestCase):
    def test_share_metadata_points_to_the_packaged_cover_with_correct_dimensions(self):
        with closing(TestClient(app)) as client:
            meta = Assets(client.get("/").text).meta
            self.assertEqual(meta["og:url"], "https://lkevaer.xyz/")
            self.assertIn("队友评分", meta["og:title"])
            self.assertIn("近12场", meta["og:description"])
            cover = urlsplit(meta["og:image"])
            self.assertEqual(cover.scheme, "https")
            self.assertEqual(cover.netloc, "lkevaer.xyz")
            response = client.get(cover.path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], meta["og:image:type"])
            with Image.open(BytesIO(response.content)) as image:
                self.assertEqual(image.size, (int(meta["og:image:width"]), int(meta["og:image:height"])))

    def test_home_references_complete_assets_matching_the_deployed_bytes(self):
        with closing(TestClient(app)) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("must-revalidate", response.headers["cache-control"])
            assets = Assets(response.text).assets
            self.assertEqual(len(assets), 6)
            for url, integrity in assets:
                with self.subTest(url=url):
                    asset = client.get(url)
                    self.assertEqual(asset.status_code, 200)
                    digest = hashlib.sha256(asset.content).digest()
                    self.assertEqual(url.split("?v=")[1], digest.hex()[:16])
                    self.assertEqual(integrity, "sha256-" + base64.b64encode(digest).decode("ascii"))

    def test_asset_change_automatically_changes_its_url_and_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "batch_rating.html").write_text('<link href="/static/example.css">', encoding="utf-8")
            css = folder / "example.css"
            css.write_text("body{color:red}", encoding="utf-8")
            first = Assets(rating_page(folder).body.decode()).assets[0]
            css.write_text("body{color:tan}", encoding="utf-8")
            second = Assets(rating_page(folder).body.decode()).assets[0]
            self.assertNotEqual(first[0], second[0])
            self.assertNotEqual(first[1], second[1])

    def test_missing_asset_is_detected_before_serving_a_partial_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "batch_rating.html").write_text('<script src="/static/missing.js"></script>', encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                rating_page(folder)

    def test_bundled_render_images_survive_packaging_and_decode(self):
        project = STATIC_DIR.parents[1]
        resources = project / "overstats/res"
        required = [resources / name for name in ("comp.png", "dps.png", "tank.png", "healer.png", "profilebg.png", "score_board.png")]
        required += [resources / "rank_flat" / f"{tier}.png" for tier in range(1, 10)]
        required += [project / "overstats/src/modules/dashen_summary/runtime/bg.png"]
        for path in required:
            with self.subTest(asset=str(path.relative_to(project))):
                with Image.open(path) as image:
                    image.verify()

    def test_static_html_entry_redirects_to_versioned_home(self):
        with closing(TestClient(app, follow_redirects=False)) as client:
            response = client.get("/static/batch_rating.html?task=example")
            self.assertEqual(response.status_code, 307)
            self.assertEqual(response.headers["location"], "/?task=example")
