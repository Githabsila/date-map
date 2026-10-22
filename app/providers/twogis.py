"""2ГИС: Places API (поиск организаций).

Документация: https://docs.2gis.com/ru/api/search/places/overview
Отдаёт рейтинг, число отзывов и иногда средний чек (в атрибутах организации).
"""
import re

import httpx

from .. import config
from ..models import Place, Source, price_level_from_check
from . import cache

URL = "https://catalog.api.2gis.com/3.0/items"
FIELDS = ",".join([
    "items.point", "items.address", "items.reviews", "items.rubrics",
    "items.contact_groups", "items.attribute_groups",
])
CHECK = re.compile(r"(?:чек|счёт|счет)[^\d]*(\d[\d\s]*)(?:\s*[–—-]\s*(\d[\d\s]*))?", re.IGNORECASE)


def _avg_check(item: dict) -> int | None:
    for group in item.get("attribute_groups") or []:
        for attr in group.get("attributes") or []:
            m = CHECK.search(attr.get("name", ""))
            if m:
                low = int(re.sub(r"\s", "", m.group(1)))
                high = int(re.sub(r"\s", "", m.group(2))) if m.group(2) else low
                return (low + high) // 2
    return None


def _contact(item: dict, kind: str) -> str:
    for group in item.get("contact_groups") or []:
        for c in group.get("contacts") or []:
            if c.get("type") == kind:
                return c.get("text") or c.get("value") or ""
    return ""


async def search(query: str, lat: float, lon: float, radius_m: int, client: httpx.AsyncClient) -> list[Place]:
    key = ("2gis", query, round(lat, 3), round(lon, 3), radius_m)
    if (cached := cache.get(key)) is not None:
        return cached

    r = await client.get(URL, params={
        "key": config.TWOGIS_API_KEY,
        "q": query,
        "point": f"{lon},{lat}",
        "radius": min(radius_m, 40000),
        "type": "branch",
        "fields": FIELDS,
        "locale": "ru_RU",
        "page_size": 10,  # максимум для демо-ключа 2ГИС
    }, timeout=config.HTTP_TIMEOUT_S)
    r.raise_for_status()
    data = r.json()
    code = data.get("meta", {}).get("code")
    if code == 404:  # 2ГИС отвечает 404 внутри JSON, когда ничего не нашлось
        cache.put(key, [])
        return []
    if code != 200:
        raise RuntimeError(data.get("meta", {}).get("error", {}).get("message", f"код {code}"))

    places = []
    for item in data.get("result", {}).get("items", []):
        point = item.get("point")
        if not point:
            continue
        reviews = item.get("reviews") or {}
        rating = reviews.get("general_rating")
        count = reviews.get("general_review_count")
        rubrics = item.get("rubrics") or []
        check = _avg_check(item)
        firm_id = str(item.get("id", "")).split("_")[0]
        places.append(Place(
            name=item.get("name", ""),
            lat=point["lat"], lon=point["lon"],
            address=item.get("address_name", ""),
            kind=rubrics[0].get("name", "") if rubrics else "",
            rating=float(rating) if rating else None,
            reviews=int(count) if count else None,
            avg_check=check,
            price_level=price_level_from_check(check) if check else None,
            phone=_contact(item, "phone"),
            website=_contact(item, "website"),
            sources=[Source(
                provider="2gis",
                url=f"https://2gis.ru/firm/{firm_id}",
                rating=float(rating) if rating else None,
                reviews=int(count) if count else None,
            )],
        ))
    cache.put(key, places)
    return places
