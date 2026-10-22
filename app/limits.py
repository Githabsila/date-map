"""Защита лимитов: не даём одному человеку и всем вместе израсходовать месячную квоту карт.

Счётчики живут в памяти процесса: после перезапуска сервера они обнуляются.
"""
import time
from collections import deque
from datetime import date

from . import config

_per_ip: dict[str, deque[float]] = {}
_day = date.today()
_calls_today = 0


def _allow(store: dict[str, deque[float]], ip: str, limit: int) -> bool:
    now = time.monotonic()
    q = store.setdefault(ip, deque())
    while q and now - q[0] > config.SEARCH_WINDOW_S:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    if len(store) > 10_000:
        store.clear()
    return True


def allow_ip(ip: str) -> bool:
    """Не больше SEARCHES_PER_IP поисков за SEARCH_WINDOW_S секунд с одного адреса."""
    return _allow(_per_ip, ip, config.SEARCHES_PER_IP)


_routes_per_ip: dict[str, deque[float]] = {}


def allow_route(ip: str) -> bool:
    """Маршруты дешевле поиска, но бесплатные серверы маршрутов тоже просят не злоупотреблять."""
    return _allow(_routes_per_ip, ip, config.SEARCHES_PER_IP * 4)


def reserve_calls(n: int) -> bool:
    """Резервирует n платных запросов к картам на сегодня. False — дневной бюджет исчерпан."""
    global _day, _calls_today
    if not config.DAILY_PROVIDER_CALLS:
        return True
    today = date.today()
    if today != _day:
        _day, _calls_today = today, 0
    if _calls_today + n > config.DAILY_PROVIDER_CALLS:
        return False
    _calls_today += n
    return True


def calls_today() -> int:
    return _calls_today
