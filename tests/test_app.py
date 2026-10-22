import asyncio
from datetime import date

import httpx
from fastapi.testclient import TestClient

from app import config
from app.merge import rank
from app.models import Place, SearchRequest, Source
from app.parser import rule_based_plan
from app.providers import google, twogis, yandex


def plan(text, **kw):
    return rule_based_plan(SearchRequest(text=text, **kw))


# --- разбор запроса без ИИ ---

def test_meat_cheap():
    p = plan("хочется чего то мясного но не дорого")
    assert "стейк-хаус" in p.queries
    assert p.category == "food"
    assert p.max_price_level == 1


def test_buy_figure():
    p = plan("хочу купить фигурку хелоу китти")
    assert p.category == "shopping"
    assert p.queries == ["фигурку хелоу китти", "магазин игрушек"]


def test_budget_in_rubles():
    assert plan("суши до 1000 рублей").max_price_level == 2
    assert plan("ресторан до 3 тыс").max_price_level == 3
    assert plan("что-нибудь шикарное").max_price_level == 4


def test_ui_filters_win():
    p = plan("", category="cinema", max_price_level=2)
    assert p.queries == ["кинотеатр"]
    assert p.max_price_level == 2


def test_unknown_text_becomes_query():
    p = plan("хочу на свидание в планетарий")
    assert p.queries == ["планетарий"]


def test_empty_request_gives_date_ideas():
    assert len(plan("").queries) == 3


# --- склейка и бюджет ---

def _place(name, lat, lon, provider, rating=None, reviews=None, level=None):
    return Place(name=name, lat=lat, lon=lon, rating=rating, reviews=reviews, price_level=level,
                 sources=[Source(provider=provider, url=f"https://{provider}", rating=rating, reviews=reviews)])


def test_same_place_from_two_maps_is_merged():
    a = _place("Ресторан «Угли»", 55.75, 37.61, "2gis", 4.8, 100)
    b = _place("Угли", 55.7503, 37.6102, "google", 4.4, 300)
    c = _place("Другое кафе", 55.7503, 37.6102, "yandex")
    out = rank([a, b, c], 55.75, 37.61, 0)
    merged = next(p for p in out if "Угли" in p.name)
    assert len(out) == 2
    assert {s.provider for s in merged.sources} == {"2gis", "google"}
    assert merged.rating == 4.5  # взвешено по числу отзывов


def test_budget_filter_keeps_unknown():
    cheap = _place("A", 55.75, 37.61, "2gis", level=1)
    pricey = _place("B", 55.76, 37.62, "2gis", level=4)
    unknown = _place("C", 55.77, 37.63, "yandex")
    names = [p.name for p in rank([cheap, pricey, unknown], 55.75, 37.61, 1)]
    assert names == ["A", "C"]


def test_chain_branches_do_not_flood_top():
    chain = [_place(f"The Бык, ресторан", 55.75 + i * 0.01, 37.61, "2gis", 4.9, 5000) for i in range(4)]
    other = _place("Халва, чайхана", 55.70, 37.61, "2gis", 4.6, 80)
    names = [p.name for p in rank(chain + [other], 55.75, 37.61, 0)]
    assert names[2] == "Халва, чайхана"


# --- разбор ответов карт на заготовленных данных ---

def _client(payload, status=200):
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=payload)))


def test_twogis_parsing(monkeypatch):
    monkeypatch.setattr(config, "TWOGIS_API_KEY", "test")
    payload = {"meta": {"code": 200}, "result": {"items": [{
        "id": "70000001_abc", "name": "Угли", "address_name": "Тверская, 1",
        "point": {"lat": 55.75, "lon": 37.61},
        "reviews": {"general_rating": 4.7, "general_review_count": 812},
        "rubrics": [{"name": "Стейк-хаус"}],
        "attribute_groups": [{"attributes": [{"name": "Средний чек 1000–1500 ₽"}]}],
    }]}}
    places = asyncio.run(twogis.search("стейк", 55.75, 37.61, 5000, _client(payload)))
    p = places[0]
    assert p.sources[0].url == "https://2gis.ru/firm/70000001"
    assert p.avg_check == 1250 and p.price_level == 2
    assert p.rating == 4.7 and p.kind == "Стейк-хаус"


def test_twogis_check_with_nbsp():
    item = {"attribute_groups": [{"attributes": [{"name": "Шашлычные"}, {"name": "Чек 1 500 ₽"}]}]}
    assert twogis._avg_check(item) == 1500


def test_twogis_not_found(monkeypatch):
    monkeypatch.setattr(config, "TWOGIS_API_KEY", "test")
    payload = {"meta": {"code": 404, "error": {"message": "Results not found"}}}
    assert asyncio.run(twogis.search("пусто-пусто", 1, 2, 5000, _client(payload))) == []


def test_google_parsing(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "test")
    payload = {"places": [{
        "displayName": {"text": "Угли"}, "formattedAddress": "Тверская, 1",
        "location": {"latitude": 55.75, "longitude": 37.61},
        "rating": 4.4, "userRatingCount": 300, "priceLevel": "PRICE_LEVEL_EXPENSIVE",
        "googleMapsUri": "https://maps.google.com/?cid=1",
    }]}
    p = asyncio.run(google.search("стейк", 55.75, 37.61, 5000, _client(payload)))[0]
    assert p.price_level == 3 and p.rating == 4.4


def test_yandex_parsing(monkeypatch):
    monkeypatch.setattr(config, "YANDEX_API_KEY", "test")
    payload = {"features": [{
        "geometry": {"coordinates": [37.61, 55.75]},
        "properties": {"CompanyMetaData": {
            "id": "123", "name": "Угли", "address": "Тверская, 1",
            "Categories": [{"name": "Ресторан"}], "Phones": [{"formatted": "+7 495 000-00-00"}],
        }},
    }]}
    p = asyncio.run(yandex.search("стейк", 55.75, 37.61, 5000, _client(payload)))[0]
    assert (p.lat, p.lon) == (55.75, 37.61)
    assert p.sources[0].url == "https://yandex.ru/maps/org/123/"


# --- весь сайт в демо-режиме ---

def test_api_demo_mode(monkeypatch):
    for k in ("YANDEX_API_KEY", "TWOGIS_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY"):
        monkeypatch.setattr(config, k, "")
    monkeypatch.setattr(config, "OSM_DB", config.ROOT / "no-such.db")
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        r = c.post("/api/search", json={"text": "мясное недорого", "city": "Казань"}).json()
        assert r["demo"] and r["plan"]["parser"] == "rules"
        names = [p["name"] for p in r["places"]]
        assert "Шашлычная «Мангал на углу»" in names
        assert "Стейк-хаус «Угли»" not in names  # чек 2400 ₽ не проходит бюджет до 700 ₽
        r = c.post("/api/search", json={"text": "купить фигурку hello kitty", "city": "Москва"}).json()
        assert r["places"][0]["name"] == "Магазин игрушек «Мир фигурок»"


# --- лимиты ---

def test_ip_limit_and_daily_budget(monkeypatch):
    from app import limits
    monkeypatch.setattr(config, "SEARCHES_PER_IP", 2)
    assert limits.allow_ip("1.1.1.1") and limits.allow_ip("1.1.1.1")
    assert not limits.allow_ip("1.1.1.1")
    assert limits.allow_ip("2.2.2.2")

    monkeypatch.setattr(config, "DAILY_PROVIDER_CALLS", 5)
    monkeypatch.setattr(limits, "_calls_today", 0)
    assert limits.reserve_calls(3)
    assert not limits.reserve_calls(3)
    assert limits.reserve_calls(2)


def test_api_rate_limited(monkeypatch):
    for k in ("YANDEX_API_KEY", "TWOGIS_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY"):
        monkeypatch.setattr(config, k, "")
    monkeypatch.setattr(config, "OSM_DB", config.ROOT / "no-such.db")
    monkeypatch.setattr(config, "SEARCHES_PER_IP", 1)
    from app.main import app
    with TestClient(app) as c:
        h = {"X-Forwarded-For": "9.9.9.9"}
        assert c.post("/api/search", json={"text": "кино", "city": "Москва"}, headers=h).status_code == 200
        r = c.post("/api/search", json={"text": "кино", "city": "Москва"}, headers=h)
        assert r.status_code == 429
        assert c.get("/healthz").json()["ok"]


# --- своя база OpenStreetMap ---

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def osm_db(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("build_osm_db", config.ROOT / "scripts" / "build_osm_db.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    out = tmp_path / "places.db"
    builder.main([str(Path(__file__).parent / "data" / "sample.osm")], out)
    monkeypatch.setattr(config, "OSM_DB", out)
    return out


def test_osm_rules_and_stems():
    from app.providers import osm
    assert osm.rules_for("Стейк-хаус") == osm.RULES["стейк-хаус"]
    rules = osm.rules_for("фигурку Hello Kitty")
    assert {"search~": ["hello kitty"]} in rules and {"search&": ["фигур"]} in rules
    assert {"shop": osm.TOY_SHOPS} in rules


def test_osm_search(osm_db):
    from app.providers import osm
    find = lambda q: [p.name for p in osm._search_sync(q, 55.7558, 37.6173, 5000)]
    assert find("стейк-хаус") == ["Угли"]
    assert find("фигурку Hello Kitty") == ["Мир игрушек"]
    assert find("смотровая площадка") == ["Смотровая площадка"]
    assert find("парк") == ["Сквер у дома"]
    assert find("кинотеатр") == []
    assert osm._search_sync("парк", 59.9, 30.3, 5000) == []  # другой город — вне радиуса
    assert osm.find_city("москва") == (55.7558, 37.6173)


def test_api_uses_osm_and_skips_paid_when_budget_spent(osm_db, monkeypatch):
    for k in ("YANDEX_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY"):
        monkeypatch.setattr(config, k, "")
    monkeypatch.setattr(config, "TWOGIS_API_KEY", "test")
    monkeypatch.setattr(config, "DAILY_PROVIDER_CALLS", 1)
    from app import limits
    monkeypatch.setattr(limits, "_calls_today", 1)
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/api/search", json={"text": "стейк", "city": "Москва"}).json()
    assert not r["demo"]
    assert r["providers"] == ["OpenStreetMap"]
    assert "2ГИС" in r["errors"]
    assert [p["name"] for p in r["places"]] == ["Угли"]


# --- перепроверка результатов ---

from app.models import SearchPlan
from app.verify import verify


def _p(name, kind, lat=55.7558, lon=37.6173, provider="2gis", year=None, rating=None):
    return Place(name=name, kind=kind, lat=lat, lon=lon, rating=rating, updated_year=year,
                 sources=[Source(provider=provider, url="")])


def test_verify_shopping_flags_cafe_and_confirms_shop():
    plan = SearchPlan(queries=["фигурку hello kitty", "магазин игрушек"], category="shopping",
                      max_price_level=0, summary="")
    shop = _p("Hello Kitty Store", "Игрушки")
    cafe = _p("Hello Kitty and friends", "Кафе")
    toys = _p("Детский мир", "Магазин игрушек")
    bank = _p("Сбербанк", "Банк")
    kept, report = verify([shop, cafe, toys, bank], plan, 55.7558, 37.6173, 15000)
    by = {p.name: p for p in kept}
    assert by["Hello Kitty Store"].match == "exact"
    assert by["Hello Kitty and friends"].match == "maybe"
    assert "кафе, а не магазин" in by["Hello Kitty and friends"].warnings[0]
    assert by["Детский мир"].match == "maybe" and "неизвестно" in by["Детский мир"].warnings[0]
    assert "Сбербанк" not in by
    assert report["removed"] == {"не подходит под запрос": 1}


def test_verify_removes_far_and_wrong_type():
    plan = SearchPlan(queries=["кинотеатр"], category="cinema", max_price_level=0, summary="")
    ok = _p("Синема 5", "Кинотеатры")
    far = _p("Киномакс", "Кинотеатр", lat=56.5)  # ~80 км от центра
    wrong = _p("Кафе у кино", "Кафе")  # «кино» в названии, но рубрика — кафе
    pizza = _p("Додо", "Пиццерия")
    kept, report = verify([ok, far, wrong, pizza], plan, 55.7558, 37.6173, 15000)
    names = [p.name for p in kept]
    assert names == ["Синема 5"]
    assert report["removed"] == {"далеко от выбранного места": 1, "не подходит под запрос": 2}


def test_verify_warns_about_stale_osm_data():
    plan = SearchPlan(queries=["парк"], category="park", max_price_level=0, summary="")
    old = _p("Сквер", "Парк", provider="osm", year=2015)
    fresh = _p("Парк Горького", "Парк", provider="osm", year=date.today().year)
    kept, _ = verify([old, fresh], plan, 55.7558, 37.6173, 15000)
    by = {p.name: p for p in kept}
    assert "не обновлялись с 2015" in by["Сквер"].warnings[0]
    assert by["Парк Горького"].warnings == []


def test_nearest_city_and_api_reports_check(osm_db, monkeypatch):
    from app.providers import osm
    assert osm.nearest_city(55.76, 37.62) == "Москва"
    for k in ("YANDEX_API_KEY", "TWOGIS_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY"):
        monkeypatch.setattr(config, k, "")
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/nearest", params={"lat": 55.76, "lon": 37.62}).json() == {"city": "Москва"}
        r = c.post("/api/search", json={"text": "стейк", "lat": 55.756, "lon": 37.617}).json()
    assert r["center"]["name"] == "Москва"
    assert r["check"]["kept"] == 1 and r["places"][0]["match"] == "type"


def test_verify_drops_ticket_offices_and_flags_odd_descriptors():
    plan = SearchPlan(queries=["кинотеатр"], category="cinema", max_price_level=0, summary="")
    cinema = _p("Каро 8 Галерея, кинотеатр", "Кинотеатры")
    kiosk = _p("Каро 8 Галерея, терминал по продаже кинобилетов", "Кинотеатры")
    anticafe = _p("Учёбы.нет, антикафе", "Кинотеатры")
    kept, report = verify([cinema, kiosk, anticafe], plan, 55.7558, 37.6173, 15000)
    by = {p.name: p for p in kept}
    assert by["Каро 8 Галерея, кинотеатр"].match == "type"
    assert by["Учёбы.нет, антикафе"].match == "maybe"
    assert "Каро 8 Галерея, терминал по продаже кинобилетов" not in by
    assert report["removed"] == {"это не само место (касса, офис, аренда…)": 1}


def test_search_widens_radius_when_nothing_near(osm_db, monkeypatch):
    for k in ("YANDEX_API_KEY", "TWOGIS_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY"):
        monkeypatch.setattr(config, k, "")
    from app.main import app
    with TestClient(app) as c:
        # «Угли» в ~25 км от точки поиска: в радиусе 15 км нет, в 40 км — есть
        r = c.post("/api/search", json={"text": "стейк", "lat": 55.98, "lon": 37.6173}).json()
    assert r["check"]["widened_km"] == 40
    assert [p["name"] for p in r["places"]] == ["Угли"]


# --- бесплатная нейросеть ---

from app import llm


def test_parse_plan_json_handles_messy_answers():
    p = llm.parse_plan_json('Вот ответ:\n```json\n{"queries": "корейский ресторан", "category": "food", '
                            '"max_price_level": "1", "summary": "Корейская еда"}\n```')
    assert p.queries == ["корейский ресторан"] and p.category == "food" and p.max_price_level == 1
    assert llm.parse_plan_json('{"queries": ["кино"], "category": "movies"}').category == "any"
    assert llm.parse_plan_json("не знаю") is None
    assert llm.parse_plan_json('{"queries": [}') is None


def test_free_llm_presets(monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "gsk_test")
    assert llm.free_llm_settings()[0] == "https://api.groq.com/openai/v1"
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-or-test")
    assert llm.free_llm_settings()[0] == "https://openrouter.ai/api/v1"
    monkeypatch.setattr(config, "LLM_MODEL", "my-model")
    assert llm.free_llm_settings()[1] == "my-model"


def test_free_llm_plan_with_cache_and_ui_filters(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "gsk_test")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    monkeypatch.setattr(llm, "_cache", llm.OrderedDict())
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer gsk_test"
        content = '{"queries": ["корейский ресторан", "кафе корейской кухни"], "category": "food", ' \
                  '"max_price_level": 2, "summary": "Ищу корейскую еду"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    req = SearchRequest(text="корейские вкусняшки", max_price_level=1)
    plan = asyncio.run(llm.llm_plan(req, client))
    again = asyncio.run(llm.llm_plan(req, client))
    assert plan.queries == ["корейский ресторан", "кафе корейской кухни"]
    assert plan.max_price_level == 1  # бюджет из фильтра важнее догадки модели
    assert again.queries == plan.queries and len(calls) == 1  # второй раз — из кэша


def test_free_llm_failure_falls_back(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "gsk_test")
    monkeypatch.setattr(llm, "_cache", llm.OrderedDict())
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, json={})))
    assert asyncio.run(llm.llm_plan(SearchRequest(text="кино"), client)) is None


# --- маршруты ---

from app import routing


def test_routes_from_osrm_and_estimates(monkeypatch):
    monkeypatch.setattr(routing.cache, "_store", {})

    def handler(request):
        profile = request.url.path.split("/")[1].replace("routed-", "")
        dist = {"car": 3000, "foot": 2000, "bike": 2200}[profile]
        dur = {"car": 420, "foot": 1500, "bike": 600}[profile]
        return httpx.Response(200, json={"code": "Ok", "routes": [{
            "distance": dist, "duration": dur,
            "geometry": {"coordinates": [[37.61, 55.75], [37.62, 55.76]]}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    routes = {r["mode"]: r for r in asyncio.run(routing.build_routes((55.75, 37.61), (55.76, 37.62), client))}
    assert list(routes) == ["foot", "bus", "car", "bike", "scooter"]
    assert routes["car"]["exact"] and routes["car"]["duration_s"] == 420
    assert routes["foot"]["geometry"][0] == [55.75, 37.61]  # [lat, lon] для Leaflet
    assert routes["scooter"]["distance_m"] == 2200 and routes["scooter"]["duration_s"] < 600
    assert not routes["bus"]["exact"] and "rtt=mt" in routes["bus"]["yandex"]
    assert "rtt=sc" in routes["scooter"]["yandex"]


def test_routes_fall_back_to_estimates(monkeypatch):
    monkeypatch.setattr(routing.cache, "_store", {})
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(502)))
    routes = asyncio.run(routing.build_routes((55.75, 37.61), (55.80, 37.61), client))
    assert all(not r["exact"] and r["duration_s"] > 0 for r in routes)
    foot = next(r for r in routes if r["mode"] == "foot")
    assert foot["far"]  # 5.5 км по прямой ×1.3 — далеко для прогулки


def test_route_api_rejects_far(monkeypatch):
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/api/route", params={"from_lat": 55.75, "from_lon": 37.61, "to_lat": 45.03, "to_lon": 38.97})
    assert r.status_code == 400


def test_verify_mixed_queries_keep_matching_type():
    plan = SearchPlan(queries=["кофейня", "какао"], category="any", max_price_level=0, summary="")
    coffee = _p("Зерно", "Кофейня")
    cocoa = _p("Какао-бар Шоколадница", "Кафе")
    bank = _p("Альфа", "Банк")
    kept, _ = verify([coffee, cocoa, bank], plan, 55.7558, 37.6173, 15000)
    by = {p.name: p for p in kept}
    assert by["Зерно"].match == "type"
    assert by["Какао-бар Шоколадница"].match == "exact"
    assert "Альфа" not in by


def test_verify_accepts_places_matching_queries_even_if_category_differs():
    plan = SearchPlan(queries=["парк", "пешеходная прогулка"], category="active", max_price_level=0, summary="")
    park = _p("Чистяковская роща", "Парки")
    bowling = _p("Страйк", "Боулинг")
    bank = _p("Сбер", "Банк")
    kept, _ = verify([park, bowling, bank], plan, 55.7558, 37.6173, 15000)
    assert sorted(p.name for p in kept) == ["Страйк", "Чистяковская роща"]
