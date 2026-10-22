"""Координаты городов. Если города нет в списке, ищем его через геокодер OpenStreetMap (Nominatim)."""
import httpx

from . import config

CITIES: dict[str, tuple[float, float]] = {
    "Москва": (55.7558, 37.6173),
    "Санкт-Петербург": (59.9386, 30.3141),
    "Новосибирск": (55.0302, 82.9204),
    "Екатеринбург": (56.8389, 60.6057),
    "Казань": (55.7963, 49.1088),
    "Нижний Новгород": (56.3269, 44.0059),
    "Челябинск": (55.1644, 61.4368),
    "Самара": (53.1959, 50.1002),
    "Омск": (54.9885, 73.3242),
    "Ростов-на-Дону": (47.2357, 39.7015),
    "Уфа": (54.7388, 55.9721),
    "Красноярск": (56.0153, 92.8932),
    "Воронеж": (51.6608, 39.2003),
    "Пермь": (58.0105, 56.2502),
    "Волгоград": (48.7080, 44.5133),
    "Краснодар": (45.0355, 38.9753),
    "Сочи": (43.5855, 39.7231),
    "Калининград": (54.7104, 20.4522),
    "Владивосток": (43.1155, 131.8855),
    "Тюмень": (57.1522, 65.5272),
    "Минск": (53.9006, 27.5590),
    "Алматы": (43.2389, 76.8897),
    "Астана": (51.1694, 71.4491),
}

_geocode_cache: dict[str, tuple[float, float] | None] = {}


async def resolve_city(name: str, client: httpx.AsyncClient) -> tuple[float, float] | None:
    clean = name.strip()
    for city, coords in CITIES.items():
        if city.lower() == clean.lower():
            return coords
    from .providers import osm  # своя база городов из OpenStreetMap, без внешних запросов
    if (coords := osm.find_city(clean)) is not None:
        return coords
    key = clean.lower()
    if key in _geocode_cache:
        return _geocode_cache[key]
    try:
        r = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": clean, "format": "json", "limit": 1, "accept-language": "ru"},
            headers={"User-Agent": "date-map/0.1 (pet project)"},
            timeout=config.HTTP_TIMEOUT_S,
        )
        r.raise_for_status()
        data = r.json()
        coords = (float(data[0]["lat"]), float(data[0]["lon"])) if data else None
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None
    _geocode_cache[key] = coords
    return coords
