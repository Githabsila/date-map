from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "any", "food", "coffee", "bar", "cinema", "theatre", "museum", "park",
    "active", "quest", "karaoke", "viewpoint", "shopping",
]

# Уровни цены: средний чек на одного человека
PRICE_LEVELS = {1: "до 700 ₽", 2: "до 1500 ₽", 3: "до 3000 ₽", 4: "дороже 3000 ₽"}


def price_level_from_check(rub: int) -> int:
    if rub <= 700:
        return 1
    if rub <= 1500:
        return 2
    if rub <= 3000:
        return 3
    return 4


class SearchPlan(BaseModel):
    """Во что превратился запрос пользователя: фразы для поиска в картах и фильтры."""
    queries: list[str] = Field(description="1–3 короткие фразы для поиска в картах, например «стейк-хаус»")
    category: Category = Field(description="Тип места")
    max_price_level: int = Field(description="0 — бюджет не важен, 1 — до 700 ₽, 2 — до 1500 ₽, 3 — до 3000 ₽, 4 — любой")
    summary: str = Field(description="Одна фраза для пользователя: что ищем")


class SearchRequest(BaseModel):
    text: str = ""
    city: str = "Москва"
    lat: float | None = None  # геолокация пользователя, если разрешил
    lon: float | None = None
    category: Category = "any"
    max_price_level: int = 0
    food: str = ""  # «что хочешь поесть»


class Source(BaseModel):
    provider: Literal["yandex", "2gis", "google", "osm", "demo"]
    url: str
    rating: float | None = None
    reviews: int | None = None


class Place(BaseModel):
    name: str
    lat: float
    lon: float
    address: str = ""
    kind: str = ""  # рубрика из карт: «Ресторан», «Кофейня»…
    rating: float | None = None
    reviews: int | None = None
    price_level: int | None = None
    avg_check: int | None = None
    phone: str = ""
    website: str = ""
    distance_km: float | None = None
    sources: list[Source] = []
