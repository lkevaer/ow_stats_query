"""Package public hero/map icons for same-origin, offline-capable web reports.

Run from the repository root: python scripts/sync_game_assets.py
Only the public hero/map fields are read; no account configuration is exported.
"""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from overstats.src.modules.query_tool import get_cached_asset_path, load_query_tool

STATIC = ROOT / "api_server/static"


def main():
    config = load_query_tool()
    entries = {"heroes": [], "maps": [], "ranks": []}
    jobs = {}
    for section, kind in (("heroList", "heroes"), ("mapList", "maps"), ("rankList", "ranks")):
        for row in config.get(section, []):
            sources = list(dict.fromkeys(row.get(key) for key in ("icon", "smallIconUrl") if str(row.get(key) or "").startswith("https://")))
            entry = {"name": row.get("name") or row.get("rankName") or str(row.get("rank", "")), "guid": str(row.get("heroGuid") or row.get("guid") or row.get("rank", "")), "sources": sources}
            entries[kind].append(entry)
            if sources:
                jobs.setdefault((sources[0], kind), sources)
    resolved = {}

    def download(job):
        (url, kind), sources = job
        prefix = "game-icons/" + kind + "/" + hashlib.sha256(url.encode()).hexdigest()[:20]
        existing = list((STATIC / "game-icons" / kind).glob(Path(prefix).name + ".webp"))
        for path in existing:
            try:
                with Image.open(path) as image:
                    maximum = (400, 240) if kind == "maps" else (256, 256)
                    if image.width > maximum[0] or image.height > maximum[1]:
                        continue
                    image.verify()
                return (url, kind), "/static/" + path.relative_to(STATIC).as_posix()
            except (OSError, ValueError):
                pass
        for source in sources:
            try:
                cached = get_cached_asset_path(source, kind)
                if cached:
                    raw = cached.read_bytes()
                else:
                    with httpx.Client(follow_redirects=True, timeout=20) as client:
                        response = client.get(source)
                        response.raise_for_status()
                        raw = response.content
                with Image.open(BytesIO(raw)) as image:
                    image.load()
                    normalized = ImageOps.exif_transpose(image).convert("RGBA")
                normalized.thumbnail((400, 240) if kind == "maps" else (256, 256), Image.Resampling.LANCZOS)
                output = BytesIO()
                normalized.save(output, format="WEBP", quality=90, method=6)
                destination = STATIC / (prefix + ".webp")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(output.getvalue())
                return (url, kind), "/static/" + destination.relative_to(STATIC).as_posix()
            except (OSError, ValueError, KeyError, httpx.HTTPError):
                continue
        raise RuntimeError(f"Could not localize {kind} icon from {urlsplit(url).hostname}")

    with ThreadPoolExecutor(max_workers=6) as executor:
        for key, path in executor.map(download, jobs.items()):
            resolved[key] = path
    for kind, rows in entries.items():
        for row in rows:
            row["path"] = resolved.get((row["sources"][0], kind)) if row["sources"] else "/static/game-icons/" + {"maps":"map", "heroes":"hero", "ranks":"rank"}[kind] + "-default.svg"
    path = STATIC / "game_assets.js"
    source = path.read_text(encoding="utf-8")
    source, count = re.subn(r"(?<=/\* ASSET_CATALOG_START \*/).*?(?=/\* ASSET_CATALOG_END \*/)",
                            lambda _: json.dumps(entries, ensure_ascii=False, separators=(",", ":")), source, flags=re.S)
    assert count == 1
    path.write_bytes(source.encode("utf-8"))
    print(json.dumps({"heroes": len(entries["heroes"]), "maps": len(entries["maps"]), "ranks": len(entries["ranks"]), "unique_images": len(resolved),
                      "map_placeholders": [r["name"] for r in entries["maps"] if not r["sources"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
