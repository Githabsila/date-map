"""Google: Places API (New), Text Search.

Документация: https://developers.google.com/maps/documentation/places/web-service/text-search
Отдаёт рейтинг, число отзывов и уровень цен.
"""
import httpx

from .. import config
from ..models import Place, Source, price_level_from_check
from . import cache

URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.location",
    "places.rating", "places.userRatingCount", "places.priceLevel", "places.priceRange",
    "places.googleMapsUri", "places.primaryTypeDisplayName", "places.websiteUri",
    "places.nationalPhoneNumber",
])
PRICE_LEVEL = {
    "PRICE_LEVEL_FREE": 1,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}


def _avg_check(p: dict) -> int | None:
    rng = p.get("priceRange") or {}
    start = (rng.get("startPrice") or {})
    end = (rng.get("endPrice") or {})
    if start.get("currencyCode") != "RUB":
        return None
    low = int(start.get("units", 0))
    high = int(end.get("units", low)) if end else low
    return (low + high) // 2 or None


async def search(query: str, lat: float, lon: float, radius_m: int, client: httpx.AsyncClient) -> list[Place]:
    key = ("google", query, round(lat, 3), round(lon, 3), radius_m)
    if (cached := cache.get(key)) is not None:
        return cached

    r = await client.post(URL, headers={
        "X-Goog-Api-Key": config.GOOGLE_API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }, json={
        "textQuery": query,
        "languageCode": "ru",
        "pageSize": 20,
        "locationBias": {"circle": {
            "center": {"latitude": lat, "longitude": lon},
            "radius": float(min(radius_m, 50000)),
        }},
    }, timeout=config.HTTP_TIMEOUT_S)
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except ValueError:
            msg = r.text
        raise RuntimeError(f"HTTP {r.status_code}: {msg[:200]}")

    places = []
    for p in r.json().get("places", []):
        loc = p.get("location")
        if not loc:
            continue
        check = _avg_check(p)
        level = PRICE_LEVEL.get(p.get("priceLevel", ""))
        if check:
            level = price_level_from_check(check)
        places.append(Place(
            name=(p.get("displayName") or {}).get("text", ""),
            lat=loc["latitude"], lon=loc["longitude"],
            address=p.get("formattedAddress", ""),
            kind=(p.get("primaryTypeDisplayName") or {}).get("text", ""),
            rating=p.get("rating"),
            reviews=p.get("userRatingCount"),
            price_level=level,
            avg_check=check,
            phone=p.get("nationalPhoneNumber", ""),
            website=p.get("websiteUri", ""),
            sources=[Source(
                provider="google",
                url=p.get("googleMapsUri", ""),
                rating=p.get("rating"),
                reviews=p.get("userRatingCount"),
            )],
        ))
    cache.put(key, places)
    return places
