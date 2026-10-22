import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


YANDEX_API_KEY = _env("YANDEX_API_KEY")
TWOGIS_API_KEY = _env("TWOGIS_API_KEY")
GOOGLE_API_KEY = _env("GOOGLE_API_KEY")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _env("CLAUDE_MODEL") or "claude-opus-5-5"

# Своя база мест из OpenStreetMap (scripts/build_osm_db.py). Нет файла — источник выключен.
OSM_DB = Path(_env("OSM_DB") or ROOT / "data" / "places.db")

# Радиус поиска вокруг центра города и вокруг геолокации пользователя, метры
CITY_RADIUS_M = 15000
NEARBY_RADIUS_M = 5000

# Сколько поисковых фраз максимум отправляем в каждую карту за один запрос
MAX_QUERIES = 3
HTTP_TIMEOUT_S = 8.0
CACHE_TTL_S = 600


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


# Поисков с одного IP за окно времени
SEARCHES_PER_IP = _int("SEARCHES_PER_IP", 15)
SEARCH_WINDOW_S = _int("SEARCH_WINDOW_S", 600)
# Сколько запросов к картам (кроме кэша) можно сделать за сутки. 0 — без ограничения.
# Демо-ключ 2ГИС: 1000 запросов в месяц → ~30 в день.
DAILY_PROVIDER_CALLS = _int("DAILY_PROVIDER_CALLS", 30)


def demo_mode() -> bool:
    return not (YANDEX_API_KEY or TWOGIS_API_KEY or GOOGLE_API_KEY or OSM_DB.exists())
