"""OpenStreetMap: поиск по своей базе data/places.db (собирается scripts/build_osm_db.py).

Бесплатно, без ключей и лимитов. Рейтингов и среднего чека в OpenStreetMap нет.
"""
import asyncio
import math
import re
import sqlite3

import httpx

from .. import config
from ..models import Place, Source

# Поисковая фраза → варианты условий. Ключ «колонка» — точное совпадение с одним из значений,
# «колонка~» — подстрока (для cuisine вида "steak_house;grill").
FOOD = ["restaurant", "cafe", "fast_food", "bar", "pub", "food_court"]
RULES: dict[str, list[dict[str, list[str]]]] = {
    "ресторан": [{"amenity": ["restaurant"]}],
    "романтический ресторан": [{"amenity": ["restaurant"], "cuisine~": ["french", "italian", "european", "wine"]},
                               {"amenity": ["restaurant"]}],
    "стейк-хаус": [{"amenity": FOOD, "cuisine~": ["steak", "grill", "barbecue", "meat"]}],
    "шашлычная": [{"amenity": FOOD, "cuisine~": ["shashlik", "kebab", "barbecue", "caucasian", "grill"]}],
    "бургерная": [{"amenity": FOOD, "cuisine~": ["burger"]}],
    "суши-бар": [{"amenity": FOOD, "cuisine~": ["sushi", "japanese"]}],
    "пиццерия": [{"amenity": FOOD, "cuisine~": ["pizza"]}],
    "итальянский ресторан": [{"amenity": FOOD, "cuisine~": ["italian", "pasta"]}],
    "грузинский ресторан": [{"amenity": FOOD, "cuisine~": ["georgian"]}],
    "раменная": [{"amenity": FOOD, "cuisine~": ["ramen", "noodle"]}],
    "паназиатский ресторан": [{"amenity": FOOD, "cuisine~": ["asian", "chinese", "thai", "vietnamese", "wok", "noodle"]}],
    "корейский ресторан": [{"amenity": FOOD, "cuisine~": ["korean"]}],
    "рыбный ресторан": [{"amenity": FOOD, "cuisine~": ["seafood", "fish"]}],
    "вегетарианское кафе": [{"amenity": FOOD, "cuisine~": ["vegetarian", "vegan"]}],
    "завтраки": [{"amenity": ["cafe", "restaurant"], "cuisine~": ["breakfast", "brunch"]}],
    "кондитерская": [{"shop": ["pastry", "confectionery"]}, {"amenity": ["cafe"], "cuisine~": ["dessert", "cake", "pastry"]}],
    "мороженое": [{"amenity": ["ice_cream"]}, {"shop": ["ice_cream"]}],
    "шаурма": [{"amenity": FOOD, "cuisine~": ["shawarma", "kebab"]}],
    "пельменная": [{"amenity": FOOD, "cuisine~": ["pelmeni", "dumpling"]}],
    "блинная": [{"amenity": FOOD, "cuisine~": ["pancake", "crepe", "blini"]}],
    "кофейня": [{"amenity": ["cafe"], "cuisine~": ["coffee"]}, {"amenity": ["cafe"], "search~": ["кофе", "coffee"]}],
    "чайная": [{"amenity": ["cafe"], "cuisine~": ["tea"]}],
    "бар": [{"amenity": ["bar", "pub"]}],
    "коктейльный бар": [{"amenity": ["bar"]}],
    "винный бар": [{"amenity": ["bar", "restaurant"], "search~": ["вин", "wine"]}],
    "пивной бар": [{"amenity": ["pub"]}, {"amenity": ["bar"], "search~": ["пив", "beer"]}],
    "кинотеатр": [{"amenity": ["cinema"]}],
    "театр": [{"amenity": ["theatre"]}],
    "музей": [{"tourism": ["museum"]}],
    "выставка": [{"tourism": ["gallery"]}, {"amenity": ["arts_centre"]}],
    "галерея": [{"tourism": ["gallery"]}],
    "парк": [{"leisure": ["park", "garden"]}],
    "набережная": [{"leisure": ["park"], "search~": ["набережн"]}, {"tourism": ["viewpoint"]}],
    "каток": [{"leisure": ["ice_rink"]}],
    "боулинг": [{"leisure": ["bowling_alley"]}, {"sport~": ["bowling"]}],
    "бильярд": [{"sport~": ["billiards"]}],
    "скалодром": [{"sport~": ["climbing"]}],
    "батутный центр": [{"leisure": ["trampoline_park"]}],
    "картинг": [{"sport~": ["karting"]}],
    "квест": [{"leisure": ["escape_game"]}],
    "караоке": [{"amenity": ["karaoke_box"]}, {"search~": ["караоке", "karaoke"]}],
    "смотровая площадка": [{"tourism": ["viewpoint"]}],
    "ресторан с панорамным видом": [{"tourism": ["viewpoint"]}],
    "ресторан на крыше": [{"tourism": ["viewpoint"]}],
    "экскурсия по крышам": [{"tourism": ["viewpoint"]}],
    "мастер-класс": [{"search~": ["мастер-класс", "мастерская"]}],
    "антикафе": [{"search~": ["антикафе", "anticafe"]}],
    "торговый центр": [{"shop": ["mall"]}],
}
TOY_WORDS = ("фигур", "игруш", "кукл", "аниме", "hello kitty", "лего", "lego", "подар", "сувенир")
TOY_SHOPS = ["toys", "gift", "games", "anime", "hobby", "model", "collector"]

KINDS = {
    "restaurant": "Ресторан", "cafe": "Кафе", "fast_food": "Фастфуд", "bar": "Бар", "pub": "Паб",
    "food_court": "Фуд-корт", "ice_cream": "Мороженое", "cinema": "Кинотеатр", "theatre": "Театр",
    "arts_centre": "Арт-центр", "karaoke_box": "Караоке", "nightclub": "Клуб", "planetarium": "Планетарий",
    "biergarten": "Пивной сад", "park": "Парк", "garden": "Сад", "ice_rink": "Каток",
    "bowling_alley": "Боулинг", "escape_game": "Квест", "trampoline_park": "Батутный центр",
    "amusement_arcade": "Игровые автоматы", "water_park": "Аквапарк", "sports_centre": "Спортцентр",
    "museum": "Музей", "gallery": "Галерея", "viewpoint": "Смотровая площадка",
    "attraction": "Достопримечательность", "zoo": "Зоопарк", "aquarium": "Океанариум",
    "theme_park": "Парк развлечений", "toys": "Игрушки", "gift": "Подарки", "mall": "Торговый центр",
    "pastry": "Кондитерская", "confectionery": "Кондитерская", "books": "Книги", "anime": "Аниме",
}
COLUMNS = {"amenity", "shop", "leisure", "tourism", "sport", "cuisine", "search"}


def available() -> bool:
    return config.OSM_DB.exists()


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{config.OSM_DB.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def _stems(text: str) -> list[str]:
    """Слова запроса без окончаний: «фигурку» → «фигур», чтобы находить «фигурки», «фигурок»."""
    words = re.findall(r"[a-zа-яё0-9]+", text.lower().replace("ё", "е"))
    out = []
    for w in words:
        if len(w) < 3:
            continue
        if re.match(r"[а-я]", w) and len(w) >= 5:
            w = w[: max(4, len(w) - 2)]
        out.append(w)
    return out


def rules_for(query: str) -> list[dict[str, list[str]]]:
    q = query.lower().strip()
    if q in RULES:
        return RULES[q]
    rules: list[dict[str, list[str]]] = []
    latin = " ".join(re.findall(r"[a-z0-9]+", q))
    if latin:
        rules.append({"search~": [latin]})
    stems = [s for s in _stems(q) if not re.match(r"[a-z0-9]", s)]
    if stems:
        rules.append({"search&": stems})  # все слова сразу
    if any(w in q for w in TOY_WORDS):
        rules.append({"shop": TOY_SHOPS})
    return rules


def _where(rule: dict[str, list[str]]) -> tuple[str, list]:
    parts, args = [], []
    for key, values in rule.items():
        col = key.rstrip("~&")
        if col not in COLUMNS:
            raise ValueError(col)
        if key.endswith("~"):
            parts.append("(" + " OR ".join(f"{col} LIKE ?" for _ in values) + ")")
            args += [f"%{v}%" for v in values]
        elif key.endswith("&"):
            parts.append("(" + " AND ".join(f"{col} LIKE ?" for _ in values) + ")")
            args += [f"%{v}%" for v in values]
        else:
            parts.append(f"{col} IN ({','.join('?' * len(values))})")
            args += values
    return " AND ".join(parts), args


def _search_sync(query: str, lat: float, lon: float, radius_m: int, limit: int = 25) -> list[Place]:
    rules = rules_for(query)
    if not rules:
        return []
    dlat = radius_m / 111_000
    dlon = radius_m / (111_000 * max(math.cos(math.radians(lat)), 0.01))
    k = math.cos(math.radians(lat)) ** 2
    conds, args = [], []
    for rule in rules:
        sql, a = _where(rule)
        conds.append(f"({sql})")
        args += a
    sql = (
        "SELECT *, ((lat-?)*(lat-?) + (lon-?)*(lon-?)*?) AS d2 FROM poi "
        "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND (" + " OR ".join(conds) + ") "
        "ORDER BY d2 LIMIT ?"
    )
    params = [lat, lat, lon, lon, k, lat - dlat, lat + dlat, lon - dlon, lon + dlon, *args, limit]
    db = _connect()
    try:
        rows = db.execute(sql, params).fetchall()
    finally:
        db.close()

    places = []
    for r in rows:
        kind_key = r["amenity"] or r["tourism"] or r["leisure"] or r["shop"] or ""
        osm_type = "node" if r["id"].startswith("n") else "way"
        places.append(Place(
            name=r["name"], lat=r["lat"], lon=r["lon"],
            address=r["address"] or "",
            kind=KINDS.get(kind_key, kind_key.replace("_", " ")),
            phone=r["phone"] or "", website=r["website"] or "",
            sources=[Source(provider="osm", url=f"https://www.openstreetmap.org/{osm_type}/{r['id'][1:]}")],
        ))
    return places


async def search(query: str, lat: float, lon: float, radius_m: int, client: httpx.AsyncClient) -> list[Place]:
    return await asyncio.to_thread(_search_sync, query, lat, lon, radius_m)


def find_city(name: str) -> tuple[float, float] | None:
    if not available():
        return None
    db = _connect()
    try:
        r = db.execute(
            "SELECT lat, lon FROM city WHERE key = ? ORDER BY population DESC LIMIT 1",
            (name.strip().lower().replace("ё", "е"),),
        ).fetchone()
    finally:
        db.close()
    return (r["lat"], r["lon"]) if r else None
