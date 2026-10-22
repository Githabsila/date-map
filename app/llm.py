"""Разбор свободного запроса через Claude. Если ключа нет или запрос не удался, возвращает None."""
import logging

import anthropic

from . import config
from .models import SearchPlan, SearchRequest

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
Если запрос вообще не про места, всё равно предложи идеи для свидания."""

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic | None:
    global _client
    if not config.ANTHROPIC_API_KEY:
        return None
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=20.0, max_retries=1)
    return _client


async def llm_plan(req: SearchRequest) -> SearchPlan | None:
    client = _get_client()
    if client is None or not (req.text.strip() or req.food.strip()):
        return None

    parts = [f"Запрос: {req.text.strip() or '—'}"]
    if req.food.strip():
        parts.append(f"Что хочет поесть: {req.food.strip()}")
    if req.category != "any":
        parts.append(f"Выбранный в фильтре тип места: {req.category}")
    if req.max_price_level:
        parts.append(f"Выбранный в фильтре бюджет: уровень {req.max_price_level}")

    try:
        response = await client.beta.messages.parse(
            model=config.CLAUDE_MODEL,
            max_tokens=2000,
            system=SYSTEM,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": "\n".join(parts)}],
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

    if response.stop_reason == "refusal" or response.parsed_output is None:
        return None
    plan = response.parsed_output
    plan.queries = [q.strip() for q in plan.queries if q.strip()][: config.MAX_QUERIES]
    if not plan.queries:
        return None
    # Явно выбранные в интерфейсе фильтры важнее догадок модели
    if req.category != "any":
        plan.category = req.category
    if req.max_price_level:
        plan.max_price_level = req.max_price_level
    plan.max_price_level = max(0, min(4, plan.max_price_level))
    return plan
