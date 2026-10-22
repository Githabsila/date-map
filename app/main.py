import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from . import config, limits
from .cities import CITIES, resolve_city
from .llm import llm_plan
from .merge import rank
from .models import SearchRequest
from .parser import rule_based_plan
from .providers import PAID, cache, enabled_providers, osm

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("date-map")

http: httpx.AsyncClient | None = None

PROVIDER_NAMES = {
    "yandex": "Яндекс Карты", "2gis": "2ГИС", "google": "Google Maps", "osm": "OpenStreetMap", "demo": "Демо",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    global http
    http = httpx.AsyncClient()
    yield
    await http.aclose()


app = FastAPI(title="Куда сходить вдвоём", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/status")
async def status():
    return {
        "demo": config.demo_mode(),
        "ai": bool(config.ANTHROPIC_API_KEY),
        "providers": {
            "osm": osm.available(),
            "yandex": bool(config.YANDEX_API_KEY),
            "2gis": bool(config.TWOGIS_API_KEY),
            "google": bool(config.GOOGLE_API_KEY),
        },
        "cities": list(CITIES),
    }


def client_ip(request: Request) -> str:
    # За прокси хостинга (Render, Caddy) реальный адрес — последний в X-Forwarded-For:
    # его дописывает сам прокси, подделать его клиент не может.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


@app.get("/healthz")
async def healthz():
    return {"ok": True, "provider_calls_today": limits.calls_today()}


@app.post("/api/search")
async def search(req: SearchRequest, request: Request):
    assert http is not None
    if not limits.allow_ip(client_ip(request)):
        raise HTTPException(429, "Слишком много поисков подряд. Подожди несколько минут.")
    if req.lat is not None and req.lon is not None:
        lat, lon, radius = req.lat, req.lon, config.NEARBY_RADIUS_M
    else:
        coords = await resolve_city(req.city, http)
        if coords is None:
            raise HTTPException(404, f"Не нашёл город «{req.city}». Проверь название.")
        lat, lon = coords
        radius = config.CITY_RADIUS_M

    plan = await llm_plan(req)
    parser = "claude"
    if plan is None:
        plan = rule_based_plan(req)
        parser = "rules"

    providers = enabled_providers()
    notes: dict[str, str] = {}
    uncached = sum(
        1 for name, _ in providers for q in plan.queries
        if name in PAID and cache.get((name, q, round(lat, 3), round(lon, 3), radius)) is None
    )
    if uncached and not limits.reserve_calls(uncached):
        # Платные карты на сегодня всё; бесплатная база продолжает работать
        free = [(n, fn) for n, fn in providers if n not in PAID]
        if not free:
            raise HTTPException(503, "Сайт исчерпал дневной лимит запросов к картам. Загляни завтра.")
        for n, _ in providers:
            if n in PAID:
                notes[PROVIDER_NAMES[n]] = "дневной лимит исчерпан, сегодня ищу без него"
        providers = free
    jobs = [(name, q, fn(q, lat, lon, radius, http)) for name, fn in providers for q in plan.queries]
    results = await asyncio.gather(*(job for _, _, job in jobs), return_exceptions=True)

    places, errors = [], notes
    for (name, query, _), result in zip(jobs, results):
        if isinstance(result, Exception):
            log.warning("%s «%s»: %r", name, query, result)
            msg = str(result)
            if isinstance(result, httpx.HTTPStatusError):
                code = result.response.status_code
                msg = "ключ не подходит или закончился лимит" if code in (401, 403, 429) else f"HTTP {code}"
            elif isinstance(result, httpx.TimeoutException):
                msg = "не ответил вовремя"
            errors[PROVIDER_NAMES[name]] = msg
        else:
            places += result

    return {
        "center": {"lat": lat, "lon": lon, "radius_m": radius},
        "plan": {**plan.model_dump(), "parser": parser},
        "demo": config.demo_mode(),
        "providers": [PROVIDER_NAMES[n] for n, _ in providers],
        "errors": errors,
        "places": [p.model_dump() for p in rank(places, lat, lon, plan.max_price_level)],
    }


app.mount("/", StaticFiles(directory=config.ROOT / "static", html=True), name="static")
