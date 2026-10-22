"""Скачивает готовую базу мест при сборке Docker-образа (для хостингов без своего диска, например Render).

    python scripts/fetch_db.py <URL файла places.db или places.db.gz>
Пустой URL — ничего не делает: сайт будет работать без OpenStreetMap.
"""
import gzip
import shutil
import sys
import urllib.request
from pathlib import Path

url = sys.argv[1] if len(sys.argv) > 1 else ""
if not url:
    print("PLACES_DB_URL не задан, база OpenStreetMap не скачивается")
    sys.exit(0)
out = Path(__file__).resolve().parent.parent / "data" / "places.db"
out.parent.mkdir(parents=True, exist_ok=True)
with urllib.request.urlopen(url) as resp, open(out, "wb") as f:
    src = gzip.GzipFile(fileobj=resp) if url.endswith(".gz") else resp
    shutil.copyfileobj(src, f, length=1 << 20)
print(f"База скачана: {out.stat().st_size / 1e6:.0f} МБ")
