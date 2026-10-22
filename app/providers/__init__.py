from .. import config
from . import demo, google, osm, twogis, yandex

PAID = {"yandex", "2gis", "google"}


def enabled_providers():
    """Список (имя, функция поиска) для источников, у которых есть ключ или база."""
    if config.demo_mode():
        return [("demo", demo.search)]
    providers = []
    if osm.available():
        providers.append(("osm", osm.search))
    if config.YANDEX_API_KEY:
        providers.append(("yandex", yandex.search))
    if config.TWOGIS_API_KEY:
        providers.append(("2gis", twogis.search))
    if config.GOOGLE_API_KEY:
        providers.append(("google", google.search))
    return providers
