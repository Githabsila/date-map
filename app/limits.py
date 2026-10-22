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


def allow_ip(ip: str) -> bool:
    """Не больше SEARCHES_PER_IP поисков за SEARCH_WINDOW_S секунд с одного адреса."""
    now = time.monotonic()
    q = _per_ip.setdefault(ip, deque())
    while q and now - q[0] > config.SEARCH_WINDOW_S:
        q.popleft()
    if len(q) >= config.SEARCHES_PER_IP:
        return False
    q.append(now)
    if len(_per_ip) > 10_000:
        _per_ip.clear()
    return True


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
