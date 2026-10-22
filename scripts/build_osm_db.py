"""Сборка своей базы мест из выгрузок OpenStreetMap (download.geofabrik.de).

    python scripts/build_osm_db.py data/raw/central-fed-district-latest.osm.pbf [ещё .pbf ...]

Создаёт data/places.db: места (кафе, рестораны, кино, парки, музеи, магазины…) и города.
База целиком заменяется; чтобы обновить данные, скачай свежие .pbf и запусти снова.
"""
import sqlite3
import sys
import time
from pathlib import Path

import osmium
import osmium.filter

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "places.db"

AMENITY = {
    "restaurant", "cafe", "fast_food", "bar", "pub", "biergarten", "ice_cream", "food_court",
    "cinema", "theatre", "arts_centre", "karaoke_box", "nightclub", "planetarium",
}
LEISURE = {
    "park", "garden", "ice_rink", "bowling_alley", "escape_game", "trampoline_park",
    "amusement_arcade", "water_park", "sports_centre", "miniature_golf", "dance",
}
TOURISM = {"museum", "gallery", "viewpoint", "attraction", "zoo", "aquarium", "theme_park"}
PLACE = {"city", "town"}
UNNAMED_OK = {("tourism", "viewpoint")}  # смотровые площадки часто без названия

SCHEMA = """
CREATE TABLE poi (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
    amenity TEXT, shop TEXT, leisure TEXT, tourism TEXT, sport TEXT, cuisine TEXT,
    address TEXT, website TEXT, phone TEXT, hours TEXT, search TEXT NOT NULL, updated INTEGER
);
CREATE TABLE city (name TEXT NOT NULL, key TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, population INTEGER);
"""
INDEXES = """
CREATE INDEX poi_lat_lon ON poi(lat, lon);
CREATE INDEX city_key ON city(key);
"""


def city_key(name: str) -> str:
    return name.strip().lower().replace("ё", "е")


def wanted(tags) -> bool:
    a, s, l, t = tags.get("amenity"), tags.get("shop"), tags.get("leisure"), tags.get("tourism")
    if not (a in AMENITY or s or l in LEISURE or t in TOURISM):
        return False
    if "name" in tags:
        return True
    return ("tourism", t) in UNNAMED_OK


def updated_year(obj) -> int | None:
    try:
        y = obj.timestamp.year
    except (AttributeError, ValueError):
        return None
    return y if y >= 2005 else None


def center(obj):
    if obj.is_node():
        return obj.location.lat, obj.location.lon
    pts = [(n.location.lat, n.location.lon) for n in obj.nodes if n.location.valid()]
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def row(obj):
    tags = obj.tags
    c = center(obj)
    if c is None:
        return None
    name = tags.get("name") or "Смотровая площадка"
    street, house = tags.get("addr:street", ""), tags.get("addr:housenumber", "")
    search = " ".join(filter(None, [
        name, tags.get("name:en"), tags.get("name:ru"), tags.get("brand"), tags.get("cuisine"),
        tags.get("amenity"), tags.get("shop"), tags.get("leisure"), tags.get("tourism"),
        tags.get("sport"), tags.get("description"),
    ])).lower().replace("ё", "е")
    kind = "n" if obj.is_node() else "w"
    return (
        f"{kind}{obj.id}", name, round(c[0], 6), round(c[1], 6),
        tags.get("amenity"), tags.get("shop"), tags.get("leisure"), tags.get("tourism"),
        tags.get("sport"), tags.get("cuisine"),
        f"{street}, {house}".strip(", ") if street else "",
        tags.get("website") or tags.get("contact:website") or "",
        tags.get("phone") or tags.get("contact:phone") or "",
        tags.get("opening_hours") or "",
        search,
        updated_year(obj),
    )


def process(pbf: Path, db: sqlite3.Connection) -> tuple[int, int]:
    pois, cities, batch = 0, 0, []
    fp = (osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY)
          .with_locations()
          .with_filter(osmium.filter.KeyFilter("amenity", "shop", "leisure", "tourism", "place")))
    for obj in fp:
        tags = obj.tags
        if obj.is_node() and tags.get("place") in PLACE and "name" in tags:
            try:
                pop = int(tags.get("population", "0").replace(" ", ""))
            except ValueError:
                pop = 0
            db.execute("INSERT INTO city VALUES (?,?,?,?,?)",
                       (tags["name"], city_key(tags["name"]), obj.location.lat, obj.location.lon, pop))
            cities += 1
            continue
        if not wanted(tags):
            continue
        r = row(obj)
        if r:
            batch.append(r)
        if len(batch) >= 5000:
            db.executemany("INSERT OR REPLACE INTO poi VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
            pois += len(batch)
            batch.clear()
    db.executemany("INSERT OR REPLACE INTO poi VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
    return pois + len(batch), cities


def main(files: list[str], out: Path = DB) -> None:
    if not files:
        sys.exit(__doc__)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    for f in files:
        t = time.time()
        pois, cities = process(Path(f), db)
        db.commit()
        print(f"{Path(f).name}: {pois} мест, {cities} городов за {time.time() - t:.0f} с")
    db.executescript(INDEXES)
    db.commit()
    db.execute("VACUUM")
    db.close()
    tmp.replace(out)
    print(f"Готово: {out} ({out.stat().st_size / 1e6:.0f} МБ)")


if __name__ == "__main__":
    main(sys.argv[1:])
