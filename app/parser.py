"""Разбор запроса без ИИ: ключевые слова → поисковые фразы, категория и бюджет.

Используется, когда нет ключа Claude или он не ответил. Справляется с типовыми
запросами («мясное недорого», «кино», «купить фигурку hello kitty»).
"""
import re

from .models import SearchPlan, SearchRequest, price_level_from_check

# (корень слова, поисковые фразы, категория)
FOOD_WORDS: list[tuple[str, list[str]]] = [
    ("мяс", ["стейк-хаус", "шашлычная"]),
    ("стейк", ["стейк-хаус"]),
    ("шашлык", ["шашлычная"]),
    ("бургер", ["бургерная"]),
    ("суши", ["суши-бар"]),
    ("ролл", ["суши-бар"]),
    ("пицц", ["пиццерия"]),
    ("паст", ["итальянский ресторан"]),
    ("итальян", ["итальянский ресторан"]),
    ("грузин", ["грузинский ресторан"]),
    ("хинкал", ["грузинский ресторан"]),
    ("рамен", ["раменная"]),
    ("азиат", ["паназиатский ресторан"]),
    ("вок", ["паназиатский ресторан"]),
    ("корей", ["корейский ресторан"]),
    ("рыб", ["рыбный ресторан"]),
    ("морепрод", ["рыбный ресторан"]),
    ("вега", ["вегетарианское кафе"]),
    ("завтрак", ["завтраки"]),
    ("десерт", ["кондитерская"]),
    ("сладк", ["кондитерская"]),
    ("торт", ["кондитерская"]),
    ("мороженое", ["мороженое"]),
    ("шаверм", ["шаурма"]),
    ("шаурм", ["шаурма"]),
    ("пельмен", ["пельменная"]),
    ("блин", ["блинная"]),
]

ACTIVITY_WORDS: list[tuple[str, list[str], str]] = [
    ("кофе", ["кофейня"], "coffee"),
    ("кофей", ["кофейня"], "coffee"),
    ("чай", ["чайная"], "coffee"),
    ("коктейл", ["коктейльный бар"], "bar"),
    ("бар", ["бар"], "bar"),
    ("вин", ["винный бар"], "bar"),
    ("пив", ["пивной бар"], "bar"),
    ("кино", ["кинотеатр"], "cinema"),
    ("фильм", ["кинотеатр"], "cinema"),
    ("театр", ["театр"], "theatre"),
    ("спектак", ["театр"], "theatre"),
    ("музе", ["музей"], "museum"),
    ("выставк", ["выставка"], "museum"),
    ("галере", ["галерея"], "museum"),
    ("парк", ["парк"], "park"),
    ("прогул", ["парк", "набережная"], "park"),
    ("погул", ["парк", "набережная"], "park"),
    ("гуля", ["парк", "набережная"], "park"),
    ("актив", ["боулинг", "скалодром", "каток"], "active"),
    ("набережн", ["набережная"], "park"),
    ("каток", ["каток"], "active"),
    ("коньк", ["каток"], "active"),
    ("боулинг", ["боулинг"], "active"),
    ("бильярд", ["бильярд"], "active"),
    ("скалодром", ["скалодром"], "active"),
    ("батут", ["батутный центр"], "active"),
    ("картинг", ["картинг"], "active"),
    ("квест", ["квест"], "quest"),
    ("караоке", ["караоке"], "karaoke"),
    ("смотров", ["смотровая площадка"], "viewpoint"),
    ("крыш", ["экскурсия по крышам", "ресторан на крыше"], "viewpoint"),
    ("вид\\b", ["смотровая площадка", "ресторан с панорамным видом"], "viewpoint"),
    ("панорам", ["ресторан с панорамным видом"], "viewpoint"),
    ("мастер-класс", ["мастер-класс"], "active"),
    ("антикафе", ["антикафе"], "active"),
    ("настолк", ["антикафе"], "active"),
]

CATEGORY_DEFAULTS: dict[str, list[str]] = {
    "food": ["ресторан"],
    "coffee": ["кофейня"],
    "bar": ["бар"],
    "cinema": ["кинотеатр"],
    "theatre": ["театр"],
    "museum": ["музей", "выставка"],
    "park": ["парк"],
    "active": ["боулинг", "каток", "скалодром"],
    "quest": ["квест"],
    "karaoke": ["караоке"],
    "viewpoint": ["смотровая площадка"],
    "shopping": ["торговый центр"],
}

DATE_IDEAS = ["романтический ресторан", "кофейня", "парк"]

CATEGORY_NAMES = {
    "any": "места", "food": "где поесть", "coffee": "кофейни", "bar": "бары",
    "cinema": "кино", "theatre": "театры", "museum": "музеи и выставки", "park": "парки",
    "active": "активный отдых", "quest": "квесты", "karaoke": "караоке",
    "viewpoint": "места с красивым видом", "shopping": "магазины",
}

CHEAP = ("недорог", "не дорог", "дешев", "дёшев", "бюджетн", "студенч", "эконом", "копейк")
MIDDLE = ("средн", "нормальн")
EXPENSIVE = ("дорог", "шикарн", "пафосн", "премиум", "роскошн", "элитн")

# Слова, которые не несут смысла для поиска в картах
STOPWORDS = re.compile(
    r"\b(хочу|хочется|хотим|хотелось бы|хотела|хотел|чего[- ]?то|что[- ]?то|куда[- ]?нибудь|"
    r"где[- ]?нибудь|можно|нибудь|сходить|пойти|поесть|покушать|сводить|с девушкой|с парнем|"
    r"с любимой|с любимым|на свидание|свидание|вдвоем|вдвоём|но|и|а|в|на|не|очень|"
    r"какое|какой|какую|где|куда|мне|нам|бы|место|места|ну|пожалуйста)\b",
    re.IGNORECASE,
)
BUY = re.compile(r"(?:купить|найти|приобрести|ищу|где продаются|где продается)\s+(.+)", re.IGNORECASE)
RUBLES = re.compile(r"(?:до|за|не больше|максимум)?\s*(\d[\d\s]{1,6})\s*(?:₽|р\b|руб|рублей|тыс|к\b)", re.IGNORECASE)


def _budget(text: str) -> int:
    t = text.lower()
    m = RUBLES.search(t)
    if m:
        amount = int(m.group(1).replace(" ", ""))
        if "тыс" in m.group(0) or re.search(r"\d\s*к\b", m.group(0)):
            amount *= 1000
        return price_level_from_check(amount)
    if any(w in t for w in CHEAP):
        return 1
    if any(w in t for w in MIDDLE):
        return 2
    if any(w in t for w in EXPENSIVE):
        return 4
    return 0


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        item = item.strip()
        if item and item.lower() not in (s.lower() for s in seen):
            seen.append(item)
    return seen


def rule_based_plan(req: SearchRequest) -> SearchPlan:
    text = f"{req.text} {req.food}".strip()
    low = text.lower()
    queries: list[str] = []
    category = req.category

    buy = BUY.search(text)
    if buy:
        item = RUBLES.sub("", buy.group(1)).strip(" .,!?")
        queries.append(item)
        category = "shopping"
    else:
        for root, phrases in FOOD_WORDS:
            if root in low:
                queries += phrases
                if category == "any":
                    category = "food"
        for root, phrases, cat in ACTIVITY_WORDS:
            if re.search(rf"\b{root}", low):  # root — регулярное выражение
                queries += phrases
                if category == "any":
                    category = cat  # type: ignore[assignment]

    if not queries and text:
        rest = STOPWORDS.sub(" ", RUBLES.sub(" ", low))
        for words in (CHEAP, MIDDLE, EXPENSIVE):
            for w in words:
                rest = re.sub(rf"\b{w}\w*", " ", rest)
        rest = re.sub(r"[^\w\s-]", " ", rest)
        rest = re.sub(r"\s+", " ", rest).strip()
        if len(rest) >= 3:
            queries.append(rest)

    if not queries:
        queries = CATEGORY_DEFAULTS.get(category, DATE_IDEAS)

    level = req.max_price_level or _budget(text)
    queries = _dedupe(queries)[:3]
    summary = f"Ищу {CATEGORY_NAMES.get(category, 'места')}: {', '.join(queries)}"
    if level:
        summary += f", бюджет {['', 'до 700 ₽', 'до 1500 ₽', 'до 3000 ₽', 'любой'][level]} на человека"
    return SearchPlan(queries=queries, category=category, max_price_level=level, summary=summary)
