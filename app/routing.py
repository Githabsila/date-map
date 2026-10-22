"""Маршруты до места: на машине, пешком, на велосипеде, самокате и общественном транспорте.

Машина, пешком и велосипед — бесплатные серверы OSRM сообщества OpenStreetMap (routing.openstreetmap.de).
Самокат — по велосипедному маршруту со скоростью самоката. Для общественного транспорта бесплатного
маршрутизатора по России нет: даём оценку по расстоянию и ссылку на Яндекс Карты.
Если сервер маршрутов недоступен, для всех видов даём оценку по прямой.
"""
import asyncio

import httpx

from . import config
from .merge import distance_m
from .providers import cache

OSRM = "https://routing.openstreetmap.de/routed-{profile}/route/v1/driving/{a};{b}"
PROFILES = {"car": "car", "foot": "foot", "bike": "bike"}
YANDEX_RTT = {"car": "auto", "bus": "mt", "foot": "pd", "bike": "bc", "scooter": "sc"}
LABELS = {"car": "На машине", "bus": "На автобусе", "foot": "Пешком", "bike": "На велосипеде", "scooter": "На самокате"}

# Средние скорости для оценки, км/ч (в городе, с остановками и светофорами)
SPEED_KMH = {"car": 25, "bus": 16, "foot": 4.5, "bike": 14, "scooter": 16}
WINDING = 1.3  # дорога длиннее прямой примерно на треть
BUS_EXTRA_MIN = 10  # дойти до остановки и подождать


def yandex_link(a: tuple[float, float], b: tuple[float, float], mode: str) -> str:
    return f"https://yandex.ru/maps/?rtext={a[0]},{a[1]}~{b[0]},{b[1]}&rtt={YANDEX_RTT[mode]}"


def _estimate(mode: str, straight_m: float) -> dict:
    dist = straight_m * WINDING
    minutes = dist / 1000 / SPEED_KMH[mode] * 60
    if mode == "bus":
        minutes += BUS_EXTRA_MIN
    return {"distance_m": round(dist), "duration_s": round(minutes * 60), "geometry": None, "exact": False}


async def _osrm(profile: str, a, b, client: httpx.AsyncClient) -> dict | None:
    key = ("route", profile, round(a[0], 4), round(a[1], 4), round(b[0], 4), round(b[1], 4))
    if (hit := cache.get(key)) is not None:
        return hit
    url = OSRM.format(profile=profile, a=f"{a[1]},{a[0]}", b=f"{b[1]},{b[0]}")
    try:
        r = await client.get(url, params={"overview": "full", "geometries": "geojson"},
                             headers={"User-Agent": "date-map/0.3 (pet project)"}, timeout=config.HTTP_TIMEOUT_S)
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if r.status_code != 200 or data.get("code") != "Ok" or not data.get("routes"):
        return None
    route = data["routes"][0]
    out = {
        "distance_m": round(route["distance"]),
        "duration_s": round(route["duration"]),
        # GeoJSON хранит [lon, lat], Leaflet ждёт [lat, lon]
        "geometry": [[round(lat, 5), round(lon, 5)] for lon, lat in route["geometry"]["coordinates"]],
        "exact": True,
    }
    cache.put(key, out)
    return out


async def build_routes(a: tuple[float, float], b: tuple[float, float], client: httpx.AsyncClient) -> list[dict]:
    straight = distance_m(a[0], a[1], b[0], b[1])
    found = dict(zip(PROFILES, await asyncio.gather(*(_osrm(p, a, b, client) for p in PROFILES.values()))))

    routes = {}
    for mode in ("car", "foot", "bike"):
        routes[mode] = found[mode] or _estimate(mode, straight)
    # Самокат едет по велосипедным путям, но быстрее велосипеда в городе
    bike = routes["bike"]
    routes["scooter"] = {
        **bike,
        "duration_s": round(bike["distance_m"] / 1000 / SPEED_KMH["scooter"] * 3600),
    }
    routes["bus"] = _estimate("bus", straight)

    out = []
    for mode in ("foot", "bus", "car", "bike", "scooter"):
        r = routes[mode]
        out.append({
            "mode": mode, "label": LABELS[mode], **r,
            "yandex": yandex_link(a, b, mode),
        })
    # Пешком дальше 6 км — уже не прогулка; помечаем, но оставляем
    for r in out:
        r["far"] = r["mode"] == "foot" and r["distance_m"] > 6000
    return out
