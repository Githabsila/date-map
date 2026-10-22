"""Разбор свободного запроса нейросетью.

Порядок: Claude (если есть ANTHROPIC_API_KEY) → бесплатная нейросеть с OpenAI-совместимым API
(LLM_API_KEY: Groq, OpenRouter и т.п.) → None, и тогда работает разбор по ключевым словам.
"""
import json
import logging
import re
from collections import OrderedDict
from typing import get_args

import anthropic
import httpx
from pydantic import ValidationError

from . import config
from .models import Category, SearchPlan, SearchRequest

log = logging.getLogger(__name__)

SYSTEM = """Ты помогаешь найти место для свидания или прогулки вдвоём. Пользователь описывает, чего хочет, \
а ты превращаешь это в поиск по Яндекс Картам, 2ГИС и Google Maps.

queries: 1–3 коротких фразы, которые реально встречаются в названиях и рубриках организаций в картах \
(«стейк-хаус», «шашлычная», «грузинский ресторан», «кофейня», «смотровая площадка», «квест»). \
Не пиши в фразы бюджет, город и слова вроде «романтично». Если человек хочет что-то купить, \
фраза — сам товар или тип магазина, а названия брендов пиши так, как их пишут магазины \
(«хелоу китти» → «Hello Kitty»).
category: тип места; any, если непонятно.
max_price_level: средний чек на человека. 0 — бюджет не назван, 1 — до 700 ₽ («недорого», «дёшево»), \
2 — до 1500 ₽, 3 — до 3000 ₽, 4 — не важно или «дорого/шикарно».
summary: одна короткая фраза по-русски, что ищем, например «Ищу недорогие мясные места».
Фразы и category должны соответствовать друг другу: если ищешь «парк», category — park, а не active.
«Что-то активное» — это боулинг, скалодром, картинг, каток, батутный центр (category active).
Если запрос вообще не про места, всё равно предложи идеи для свидания."""

# Небольшие бесплатные модели не умеют строгие схемы, поэтому формат описываем словами и примером
JSON_FORMAT = f"""
Ответь ТОЛЬКО JSON-объектом без пояснений и без markdown, ровно с такими полями:
{{"queries": ["..."], "category": "...", "max_price_level": 0, "summary": "..."}}
category — одно из: {", ".join(get_args(Category))}.
Пример: запрос «хочется чего-то мясного но недорого» →
{{"queries": ["стейк-хаус", "шашлычная"], "category": "food", "max_price_level": 1, "summary": "Ищу недорогие мясные места"}}"""

# Сервис бесплатной нейросети определяем по виду ключа: так в настройках хватает одного LLM_API_KEY
PRESETS = {
    "gsk_": ("https://api.groq.com/openai/v1", "openai/gpt-oss-20b"),
    "sk-or-": ("https://openrouter.ai/api/v1", "google/gemma-4-31b-it:free"),
}

_claude: anthropic.AsyncAnthropic | None = None
_cache: OrderedDict[str, SearchPlan] = OrderedDict()


def _user_message(req: SearchRequest) -> str:
    parts = [f"Запрос: {req.text.strip() or '—'}"]
    if req.food.strip():
        parts.append(f"Что хочет поесть: {req.food.strip()}")
    if req.category != "any":
        parts.append(f"Выбранный в фильтре тип места: {req.category}")
    if req.max_price_level:
        parts.append(f"Выбранный в фильтре бюджет: уровень {req.max_price_level}")
    return "\n".join(parts)


def provider_name() -> str | None:
    if config.ANTHROPIC_API_KEY:
        return "claude"
    if config.LLM_API_KEY:
        return "free"
    return None


def free_llm_settings() -> tuple[str, str]:
    base, model = config.LLM_BASE_URL, config.LLM_MODEL
    for prefix, (preset_base, preset_model) in PRESETS.items():
        if config.LLM_API_KEY.startswith(prefix):
            base, model = base or preset_base, model or preset_model
    return base, model


async def _claude_plan(message: str) -> SearchPlan | None:
    global _claude
    if _claude is None:
        _claude = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=20.0, max_retries=1)
    try:
        response = await _claude.beta.messages.parse(
            model=config.CLAUDE_MODEL,
            max_tokens=2000,
            system=SYSTEM,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": message}],
            output_format=SearchPlan,
        )
    except anthropic.AuthenticationError:
        log.warning("Claude: неверный ANTHROPIC_API_KEY, использую разбор по ключевым словам")
        return None
    except anthropic.RateLimitError:
        log.warning("Claude: превышен лимит запросов")
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude: ошибка API %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude: нет соединения")
        return None
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output


def parse_plan_json(text: str) -> SearchPlan | None:
    """Достаёт JSON из ответа модели (бывает обёрнут в ```json … ``` или с текстом вокруг) и проверяет его."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if isinstance(data.get("queries"), str):
            data["queries"] = [data["queries"]]
        if data.get("category") not in get_args(Category):
            data["category"] = "any"
        data["max_price_level"] = int(data.get("max_price_level") or 0)
        data.setdefault("summary", "")
        return SearchPlan.model_validate(data)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError, AttributeError):
        return None


async def _free_plan(message: str, client: httpx.AsyncClient) -> SearchPlan | None:
    base, model = free_llm_settings()
    if not base or not model:
        log.warning("Бесплатная нейросеть: не задан LLM_BASE_URL/LLM_MODEL для этого ключа")
        return None
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1000,  # у рассуждающих моделей часть токенов уходит на размышления
        "messages": [
            {"role": "system", "content": SYSTEM + "\n" + JSON_FORMAT},
            {"role": "user", "content": message},
        ],
    }
    if "groq.com" in base:
        body["response_format"] = {"type": "json_object"}
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"  # запрос простой, долго думать не нужно
    try:
        r = await client.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json=body,
            timeout=config.LLM_TIMEOUT_S,
        )
    except httpx.HTTPError as e:
        log.warning("Бесплатная нейросеть: нет ответа (%r)", e)
        return None
    if r.status_code != 200:
        log.warning("Бесплатная нейросеть: HTTP %s %s", r.status_code, r.text[:200])
        return None
    try:
        text = r.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    plan = parse_plan_json(text)
    if plan is None:
        log.warning("Бесплатная нейросеть: не удалось разобрать ответ: %s", text[:200])
    return plan


async def llm_plan(req: SearchRequest, client: httpx.AsyncClient | None = None) -> SearchPlan | None:
    provider = provider_name()
    if provider is None or not (req.text.strip() or req.food.strip()):
        return None

    message = _user_message(req)
    if message in _cache:  # одинаковые запросы не тратят лимит нейросети
        _cache.move_to_end(message)
        return _cache[message].model_copy(deep=True)

    if provider == "claude":
        plan = await _claude_plan(message)
    else:
        if client is None:
            async with httpx.AsyncClient() as own:
                plan = await _free_plan(message, own)
        else:
            plan = await _free_plan(message, client)
    if plan is None:
        return None

    plan.queries = [q.strip() for q in plan.queries if q.strip()][: config.MAX_QUERIES]
    if not plan.queries:
        return None
    # Явно выбранные в интерфейсе фильтры важнее догадок модели
    if req.category != "any":
        plan.category = req.category
    if req.max_price_level:
        plan.max_price_level = req.max_price_level
    plan.max_price_level = max(0, min(4, plan.max_price_level))

    _cache[message] = plan.model_copy(deep=True)
    if len(_cache) > 500:
        _cache.popitem(last=False)
    return plan
