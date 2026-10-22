"""Склейка одинаковых мест из разных карт, фильтр по бюджету и сортировка."""
import math
import re
from difflib import SequenceMatcher

from .models import Place

SAME_PLACE_M = 120  # точки ближе этого расстояния с похожими названиями считаем одним местом
GENERIC = re.compile(
    r"\b(ресторан|кафе|бар|кофейня|пиццерия|суши-бар|бургерная|шашлычная|стейк-хаус|"
    r"restaurant|cafe|coffee|bar|ооо|ип)\b"
)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def normalize(name: str) -> str:
    n = name.lower().replace("ё", "е")
    n = GENERIC.sub(" ", n)
    n = re.sub(r"[^\w\s]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def same_name(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return a.lower() == b.lower()
    if na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.7


def _combine(target: Place, other: Place) -> None:
    providers = {s.provider for s in target.sources}
    target.sources += [s for s in other.sources if s.provider not in providers]
    rated = [s for s in target.sources if s.rating]
    if rated:
        weights = [max(s.reviews or 1, 1) for s in rated]
        target.rating = round(sum(s.rating * w for s, w in zip(rated, weights)) / sum(weights), 1)
        target.reviews = sum(s.reviews or 0 for s in rated) or None
    for field in ("address", "kind", "phone", "website"):
        if not getattr(target, field) and getattr(other, field):
            setattr(target, field, getattr(other, field))
    if target.avg_check is None and other.avg_check is not None:
        target.avg_check = other.avg_check
    if target.price_level is None and other.price_level is not None:
        target.price_level = other.price_level


def merge_places(places: list[Place]) -> list[Place]:
    merged: list[Place] = []
    for p in places:
        for m in merged:
            if distance_m(p.lat, p.lon, m.lat, m.lon) <= SAME_PLACE_M and same_name(p.name, m.name):
                _combine(m, p)
                break
        else:
            merged.append(p.model_copy(deep=True))
    return merged


MATCH_BONUS = {"exact": 1.0, "type": 0.0, "maybe": -0.8}


def score(p: Place, budget_set: bool) -> float:
    s = p.rating if p.rating else 3.9
    s += MATCH_BONUS[p.match]
    s += 0.15 * min(math.log10((p.reviews or 0) + 1), 3)
    s += 0.25 * (len(p.sources) - 1)  # место нашлось в нескольких картах — больше доверия
    s -= 0.04 * (p.distance_km or 0)
    if budget_set and p.price_level is None:
        s -= 0.2
    return s


def prepare(places: list[Place], lat: float, lon: float, max_price_level: int) -> list[Place]:
    """Склейка дублей, фильтр бюджета, расстояние до центра поиска."""
    result = []
    for p in merge_places(places):
        if max_price_level and p.price_level and p.price_level > max_price_level:
            continue
        p.distance_km = round(distance_m(lat, lon, p.lat, p.lon) / 1000, 1)
        result.append(p)
    return result


def rank(places: list[Place], lat: float, lon: float, max_price_level: int, limit: int = 40) -> list[Place]:
    return order(prepare(places, lat, lon, max_price_level), bool(max_price_level), limit)


def order(result: list[Place], budget_set: bool, limit: int = 40) -> list[Place]:
    result = sorted(result, key=lambda p: score(p, budget_set), reverse=True)
    # Не больше двух филиалов одной сети в начале списка, остальные — в конец
    top, rest, seen = [], [], {}
    for p in result:
        key = normalize(p.name.split(",")[0])
        seen[key] = seen.get(key, 0) + 1
        (top if seen[key] <= 2 else rest).append(p)
    return (top + rest)[:limit]
