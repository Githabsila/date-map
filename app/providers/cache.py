"""Простой кэш в памяти: одинаковые запросы к картам в течение 10 минут не тратят лимит ключа."""
import time

from .. import config

_store: dict[tuple, tuple[float, object]] = {}


def get(key: tuple):
    hit = _store.get(key)
    if hit and time.monotonic() - hit[0] < config.CACHE_TTL_S:
        return hit[1]
    return None


def put(key: tuple, value) -> None:
    if len(_store) > 2000:
        _store.clear()
    _store[key] = (time.monotonic(), value)
