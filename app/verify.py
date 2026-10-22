"""Перепроверка результатов перед выдачей.

Каждое место проверяется: рядом ли оно, подходит ли по типу под запрос, совпадает ли с искомым
товаром или названием, не устарели ли данные. Неподходящие места убираются, сомнительные
помечаются «уточните», и у каждого места есть короткое объяснение, почему мы его советуем.
"""
import re
from collections import Counter
from datetime import date

from .merge import distance_m
from .models import Place, SearchPlan
from .providers.osm import RULES, _stems

# Корни слов в рубрике или названии, по которым видно, что место подходит под тип запроса
EXPECTED: dict[str, tuple[str, ...]] = {
    "food": ("ресторан", "кафе", "бар", "столов", "фастфуд", "питани", "пиццер", "суши", "бургер",
             "шашлы", "стейк", "кофе", "пекарн", "кулинар", "паб", "фуд", "рамен", "пельмен", "блин",
             "чайхан", "закусоч", "гриль", "кондитер", "шаурм", "кухн", "доставк", "бистро", "мороженое"),
    "coffee": ("кофе", "кафе", "кондитер", "пекарн", "чайн", "coffee"),
    "bar": ("бар", "паб", "пивн", "винн", "клуб", "pub"),
    "cinema": ("кино", "cinema"),
    "theatre": ("театр",),
    "museum": ("музе", "галере", "выстав", "арт", "планетари"),
    "park": ("парк", "сад", "сквер", "набережн", "бульвар", "прогул", "аллея", "роща"),
    "active": ("боулинг", "каток", "скалодром", "батут", "картинг", "бильярд", "спортцентр", "спортивн", "развлеч",
               "квест", "антикафе", "мастер", "аквапарк", "игров", "лазертаг", "пейнтбол", "клуб"),
    "quest": ("квест",),
    "karaoke": ("караоке",),
    "viewpoint": ("смотров", "достопр", "вид", "панорам", "ресторан", "мост", "крыш"),
    "shopping": ("магазин", "игруш", "подар", "сувенир", "торгов", "книг", "аниме", "хобби", "коллекц",
                 "игры", "toys", "gift", "универмаг", "детск"),
}
CATEGORY_NAME = {
    "food": "заведение, где поесть", "coffee": "кофейня", "bar": "бар", "cinema": "кинотеатр",
    "theatre": "театр", "museum": "музей", "park": "парк", "active": "место для активного отдыха",
    "quest": "квест", "karaoke": "караоке", "viewpoint": "место с видом", "shopping": "магазин",
}
# Описание после запятой в названии 2ГИС («Каро, терминал по продаже кинобилетов») выдаёт места,
# которые попали в рубрику, но сами туда не ведут
NOT_A_VENUE = ("терминал", "аренд", "касс", "билет", "пункт выдачи", "банкомат", "склад", "офис",
               "оборудован", "ремонт", "оптов", "производств", "поставщик", "школа", "курсы", "доставка")
STALE_YEARS = 4  # данные OpenStreetMap старше — просим уточнить, работает ли место
FAR_FACTOR = 1.25


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def item_terms(plan: SearchPlan) -> list[tuple[str, list[str]]]:
    """Что конкретно ищем (товар, название), кроме общего типа места.

    Фразы из словаря категорий («кинотеатр», «стейк-хаус») — это тип, а не название, их не берём.
    Возвращает пары (как показать человеку, что искать в тексте). Для «фигурку hello kitty» —
    ("hello kitty", ["hello kitty"]): латинское название надёжнее русских окончаний.
    """
    out = []
    for q in plan.queries:
        if _norm(q) in RULES:
            continue
        latin = " ".join(re.findall(r"[a-z0-9]+", _norm(q)))
        if latin:
            out.append((latin, [latin]))
        else:
            stems = [s for s in _stems(q) if len(s) >= 4]
            if stems:
                out.append((q.strip(), stems))
    return out


def implied_categories(plan: SearchPlan) -> set[str]:
    """Типы мест, которые следуют из фраз запроса: «кофейня» → coffee, «квест» → quest."""
    cats = set()
    for q in plan.queries:
        nq = _norm(q)
        cats |= {c for c, roots in EXPECTED.items() if any(r in nq for r in roots)}
    return cats


def _type_ok(p: Place, category: str) -> bool:
    roots = EXPECTED.get(category)
    if not roots:
        return False
    # Тип — по рубрике карты; по названию только если рубрики нет («Кафе у кино» — не кинотеатр)
    text = _norm(p.kind or p.name)
    return any(r in text for r in roots)


def _descriptor(p: Place) -> str:
    return _norm(p.name.split(",", 1)[1]) if "," in p.name else ""


def verify(places: list[Place], plan: SearchPlan, lat: float, lon: float, radius_m: int) -> tuple[list[Place], dict]:
    category = plan.category
    # Точное название ищем только для покупок и свободных запросов; «стейк-хаус» — это тип места
    terms = item_terms(plan) if category in ("any", "shopping") else []
    allowed = implied_categories(plan) | ({category} if category != "any" else set())
    removed: Counter[str] = Counter()
    kept: list[Place] = []
    this_year = date.today().year

    for p in places:
        if distance_m(lat, lon, p.lat, p.lon) > radius_m * FAR_FACTOR:
            removed["далеко от выбранного места"] += 1
            continue

        desc = _descriptor(p)
        if desc and category != "shopping" and any(w in desc for w in NOT_A_VENUE):
            removed["это не само место (касса, офис, аренда…)"] += 1
            continue

        text = _norm(f"{p.name} {p.kind}")
        exact_hit = next((label for label, words in terms if all(w in text for w in words)), None)
        # Место подходит, если подходит под выбранный тип или под тип хотя бы одной фразы поиска:
        # нейросеть может назвать тип «активный отдых», а искать «парк»
        type_ok = any(_type_ok(p, c) for c in allowed)
        why: list[str] = []
        warnings: list[str] = []

        if terms:
            label = exact_hit or terms[0][0]
            if exact_hit and (type_ok or category == "any"):
                p.match = "exact"
                why.append(f"в названии есть «{label}»")
            elif type_ok and category == "any":
                p.match = "type"
                why.append(p.kind)
            elif exact_hit:
                p.match = "maybe"
                what = CATEGORY_NAME.get(category, "то, что нужно")
                warnings.append(f"в названии есть «{label}», но это {p.kind.lower() or 'место другого типа'}, а не {what}")
            elif type_ok:
                p.match = "maybe"
                warnings.append(f"{p.kind or 'Подходящее место'}, но есть ли там «{label}» — неизвестно, уточните")
            else:
                removed["не подходит под запрос"] += 1
                continue
        elif type_ok and desc and not any(r in desc for r in EXPECTED.get(category, ())):
            p.match = "maybe"
            warnings.append(f"в картах отмечено как «{p.kind}», но по описанию это {desc} — уточните")
        elif type_ok:
            p.match = "type"
            why.append(p.kind or CATEGORY_NAME.get(category, ""))
        elif not p.kind:
            p.match = "maybe"
            warnings.append("тип места в картах не указан — уточните")
        elif category == "any":
            p.match = "type"
            why.append(p.kind)
        else:
            removed["не подходит под запрос"] += 1
            continue

        providers = {s.provider for s in p.sources}
        if len(providers) >= 2:
            why.append(f"есть в {len(providers)} картах")
        if p.rating and p.reviews and p.reviews >= 20:
            why.append(f"★ {p.rating} по {p.reviews} отзывам")
        if providers == {"osm"} and p.updated_year and this_year - p.updated_year >= STALE_YEARS:
            warnings.append(f"данные о месте не обновлялись с {p.updated_year} года — проверьте, что оно работает")
        if plan.max_price_level and p.price_level is None:
            warnings.append("средний чек неизвестен")

        p.why = ", ".join(w for w in why if w)
        p.warnings = warnings
        kept.append(p)

    # Если надёжных вариантов достаточно, сомнительные не показываем вовсе
    sure = [p for p in kept if p.match != "maybe"]
    if len(sure) >= 5 and category != "shopping":
        dropped = len(kept) - len(sure)
        if dropped:
            removed["есть варианты надёжнее"] += dropped
        kept = sure

    report = {"checked": len(places), "kept": len(kept), "removed": dict(removed)}
    return kept, report
