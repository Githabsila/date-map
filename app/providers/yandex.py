"""Яндекс: API Поиска по организаций (Geosearch).

Документация: https://yandex.ru/maps-api/docs/geosearch-api/
Рейтинга и среднего чека этот API не отдаёт, только название, адрес, рубрики, телефон и сайт.
"""
from urllib.parse import quote

import httpx

from .. import config
from ..models import Place, Source
from . import cache

URL = "https://search-maps.yandex.ru/v1/"


async def search(query: str, lat: float, lon: float, radius_m: int, client: httpx.AsyncClient) -> list[Place]:
    key = ("yandex", query, round(lat, 3), round(lon, 3), radius_m)
    if (cached := cache.get(key)) is not None:
        return cached

    # spn — размер области поиска в градусах (ширина, высота)
    span = radius_m / 111_000 * 2
    r = await client.get(URL, params={
        "apikey": config.YANDEX_API_KEY,
        "text": query,
        "lang": "ru_RU",
        "type": "biz",
        "ll": f"{lon},{lat}",
        "spn": f"{span:.4f},{span:.4f}",
        "rspn": 1,
        "results": 20,
    }, timeout=config.HTTP_TIMEOUT_S)
    r.raise_for_status()

    places = []
    for f in r.json().get("features", []):
        meta = f.get("properties", {}).get("CompanyMetaData", {})
        coords = f.get("geometry", {}).get("coordinates")
        if not meta or not coords:
            continue
        phones = meta.get("Phones") or []
        cats = meta.get("Categories") or []
        org_id = meta.get("id", "")
        places.append(Place(
            name=meta.get("name", ""),
            lon=coords[0], lat=coords[1],
            address=meta.get("address", ""),
            kind=cats[0].get("name", "") if cats else "",
            phone=phones[0].get("formatted", "") if phones else "",
            website=meta.get("url", ""),
            sources=[Source(
                provider="yandex",
                url=f"https://yandex.ru/maps/org/{org_id}/" if org_id
                else f"https://yandex.ru/maps/?text={quote(meta.get('name', ''))}&ll={coords[0]},{coords[1]}&z=17",
            )],
        ))
    cache.put(key, places)
    return places
