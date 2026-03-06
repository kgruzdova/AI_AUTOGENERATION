"""
Модуль взаимодействия с ИИ для анализа диалогов с клиентами.

Поддерживает: GigaChat, GenAPI (gen-api.ru), RockAPI и другие OpenAI-совместимые API.
Провайдер определяется по наличию GIGACHAT_CREDENTIALS или GENAPI_API_KEY.
"""
import json
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("report_generator.ai_processor")

SYSTEM_PROMPT = """Ты — аналитик диалогов с клиентами. Проанализируй транскрибацию диалога и извлеки структурированную информацию.

Ответь СТРОГО в формате JSON без дополнительного текста:
{
    "client_name": "Имя или обозначение клиента (если не указано — 'Клиент')",
    "topic": "Краткая тема обращения (1-2 предложения)",
    "main_request": "Основной запрос или цель клиента",
    "deadlines_and_cost": "Желаемые сроки и стоимость (если упоминались в диалоге, иначе 'Не указано')",
    "main_wishes": "Что точно должно быть в финальном продукте — основные пожелания клиента (если не указано — 'Не указано')",
    "mood": "Общее настроение клиента (спокойный, раздражённый, заинтересованный и т.д.)",
    "next_steps": "Рекомендуемые следующие шаги (3-5 пунктов)"
}"""


def _process_with_gigachat(text: str) -> str:
    """Обработка через GigaChat API."""
    from gigachat import GigaChat
    from gigachat.models import Chat, Messages, MessagesRole

    credentials = os.getenv("GIGACHAT_CREDENTIALS")
    if not credentials:
        raise ValueError(
            "GIGACHAT_CREDENTIALS не найден. Добавьте ключ в .env (получить: https://developers.sber.ru/studio)"
        )

    model = os.getenv("GIGACHAT_MODEL", "GigaChat")
    verify_ssl = os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "true").lower() in ("true", "1", "yes")

    logger.debug("Отправка запроса в GigaChat (model=%s, длина=%d)", model, len(text))
    with GigaChat(
        credentials=credentials,
        model=model,
        verify_ssl_certs=verify_ssl,
    ) as client:
        chat = Chat(
            messages=[
                Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
                Messages(role=MessagesRole.USER, content=f"Транскрибация диалога:\n\n{text}"),
            ],
        )
        response = client.chat(chat)

    return response.choices[0].message.content.strip()


def _messages_to_genapi(messages: list[dict]) -> list[dict]:
    """Преобразует сообщения в формат GenAPI: content — массив [{type, text}]."""
    result = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        result.append({"role": msg["role"], "content": content})
    return result


def _extract_text_from_genapi_output(data: dict) -> str:
    """Извлекает текст из ответа GenAPI (output/result/full_response/response/choices)."""

    def _from_val(val):
        if val is None:
            return None
        if isinstance(val, str):
            return val.strip() or None
        if isinstance(val, list):
            if not val:
                return None
            if isinstance(val[0], str):
                return " ".join(val).strip() or None
            for item in val:
                if isinstance(item, dict):
                    msg = item.get("message")
                    if isinstance(msg, dict) and "content" in msg:
                        c = msg["content"]
                        return (c.strip() if isinstance(c, str) else str(c)).strip() or None
                    t = item.get("text") or item.get("content") or item.get("message")
                    if isinstance(t, str):
                        return t.strip() or None
                    if isinstance(t, list):
                        for part in t:
                            if isinstance(part, dict) and part.get("type") == "text":
                                return (part.get("text") or "").strip() or None
        if isinstance(val, dict):
            for k in ("content", "text", "message"):
                t = _from_val(val.get(k))
                if t:
                    return t
            choices = val.get("choices")
            if choices and isinstance(choices, list) and choices[0]:
                msg = choices[0].get("message") or choices[0]
                return _from_val(msg.get("content") if isinstance(msg, dict) else msg)
        return None

    for key in ("output", "result", "full_response", "response"):
        text = _from_val(data.get(key))
        if text:
            return text
    if "choices" in data and data["choices"]:
        msg = data["choices"][0].get("message", {})
        text = _from_val(msg.get("content"))
        if text:
            return text
    raise ValueError(f"Не удалось извлечь текст из ответа GenAPI. Ключи: {list(data.keys())}")


def _process_with_genapi_native(text: str) -> str:
    """Обработка через нативный API GenAPI (POST /networks/{model})."""
    import time
    import requests

    api_key = os.getenv("GENAPI_API_KEY") or os.getenv("GENAPI_KEY")
    if not api_key:
        raise ValueError("GENAPI_API_KEY не найден в .env")

    base_url = (os.getenv("GENAPI_BASE_URL") or "https://api.gen-api.ru/api/v1").rstrip("/")
    model = os.getenv("GENAPI_MODEL", "gpt-4o-mini")

    # Убираем /openai/v1, если попал в URL — используем нативный API
    if "openai" in base_url.lower():
        base_url = "https://api.gen-api.ru/api/v1"

    url = f"{base_url}/networks/{model}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Транскрибация диалога:\n\n{text}"},
    ]
    body = {
        "messages": _messages_to_genapi(messages),
        "is_sync": True,
        "temperature": 0.3,
        "max_tokens": 1024,
    }

    logger.debug("Отправка запроса в GenAPI (model=%s, url=%s)", model, url)
    response = requests.post(url, headers=headers, json=body, timeout=120)
    response.raise_for_status()
    data = response.json()

    if data.get("status") == "success" or "response" in data or "output" in data or "result" in data:
        try:
            return _extract_text_from_genapi_output(data)
        except ValueError:
            pass

    request_id = data.get("request_id")
    if request_id is not None:
        time.sleep(2)
        for _ in range(60):
            poll_url = f"{base_url}/request/get/{request_id}"
            poll_resp = requests.get(poll_url, headers=headers, timeout=30)
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()
            if poll_data.get("status") == "success":
                return _extract_text_from_genapi_output(poll_data)
            if poll_data.get("status") in ("failed", "error"):
                raise RuntimeError(f"GenAPI ошибка: {poll_data}")
            time.sleep(2)
        raise TimeoutError("GenAPI: превышено время ожидания результата")

    raise RuntimeError(f"Неожиданный ответ GenAPI: {data}")


def _process_with_openai_compatible(text: str) -> str:
    """Обработка через OpenAI-совместимый API (RockAPI и др.)."""
    from openai import OpenAI

    api_key = os.getenv("GENAPI_API_KEY")
    base_url = (os.getenv("GENAPI_BASE_URL") or "https://api.rockapi.ru/openai/v1").rstrip("/")
    model = os.getenv("GENAPI_MODEL", "gpt-4o-mini")

    logger.debug("Отправка запроса в API (model=%s, base_url=%s)", model, base_url)
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Транскрибация диалога:\n\n{text}"},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


def process_dialog_with_ai(text: str) -> dict:
    """
    Отправляет транскрибацию диалога в ИИ и возвращает структурированные данные.
    GigaChat — при GIGACHAT_CREDENTIALS. GenAPI (нативный) — при GENAPI_API_KEY и base api.gen-api.ru.
    OpenAI-совместимый — для RockAPI и др.
    """
    if os.getenv("GIGACHAT_CREDENTIALS"):
        content = _process_with_gigachat(text)
    elif os.getenv("GENAPI_API_KEY") or os.getenv("GENAPI_KEY"):
        base_url = (os.getenv("GENAPI_BASE_URL") or "").lower()
        if "gen-api.ru" in base_url and "openai" not in base_url:
            content = _process_with_genapi_native(text)
        else:
            content = _process_with_openai_compatible(text)
    else:
        raise ValueError(
            "Задайте GIGACHAT_CREDENTIALS или GENAPI_API_KEY в .env.\n"
            "GigaChat: https://developers.sber.ru/studio\n"
            "GenAPI/RockAPI: ключ в личном кабинете провайдера"
        )

    logger.debug("Получен ответ от API (длина=%d символов)", len(content))

    # Убираем markdown-разметку
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(line for line in lines if not line.strip().startswith("```"))

    try:
        result = json.loads(content)
        logger.info("Успешно распарсен JSON от ИИ")
        return result
    except json.JSONDecodeError as e:
        logger.error("Некорректный JSON от ИИ: %s", e)
        raise ValueError(f"ИИ вернул некорректный JSON: {e}\nСодержимое: {content}")
