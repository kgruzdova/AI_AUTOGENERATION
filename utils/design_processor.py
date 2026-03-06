"""
Модуль обработки диалогов по заказам дизайна сайта.
Извлекает промпт для генерации изображения и вызывает GenAPI для генерации.
"""
import base64
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from utils.ai_processor import _extract_text_from_genapi_output, _messages_to_genapi

load_dotenv()

logger = logging.getLogger("report_generator.design_processor")

DESIGN_SYSTEM_PROMPT = """Ты — аналитик заказов на дизайн сайтов. Проанализируй транскрибацию диалога с клиентом и извлеки информацию.

Ответь СТРОГО в формате JSON без дополнительного текста:
{
    "client_name": "Имя клиента (если не указано — 'Клиент')",
    "topic": "Краткое описание заказа (дизайн какого сайта)",
    "main_request": "Основные требования к дизайну",
    "deadlines_and_cost": "Желаемые сроки и бюджет (если упоминались)",
    "main_wishes": "Что должно быть в дизайне — ключевые пожелания",
    "image_prompt": "Детальный промпт на АНГЛИЙСКОМ для генерации примера дизайна сайта: опиши макет, цвета, стиль, типичные элементы (шапка, баннер, карточки и т.д.) как для text-to-image модели. 1-3 предложения."
}"""


def process_design_dialog(text: str) -> dict:
    """
    Анализирует диалог о заказе дизайна и возвращает структурированные данные.
    """
    if not os.getenv("GENAPI_API_KEY") and not os.getenv("GENAPI_KEY"):
        raise ValueError("GENAPI_API_KEY требуется для отчёта по дизайну")

    base_url = (os.getenv("GENAPI_BASE_URL") or "https://api.gen-api.ru/api/v1").rstrip("/")
    if "openai" in base_url.lower():
        base_url = "https://api.gen-api.ru/api/v1"
    model = os.getenv("GENAPI_MODEL", "gpt-4o-mini")

    api_key = os.getenv("GENAPI_API_KEY") or os.getenv("GENAPI_KEY")
    url = f"{base_url}/networks/{model}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    messages = [
        {"role": "system", "content": DESIGN_SYSTEM_PROMPT},
        {"role": "user", "content": f"Транскрибация диалога:\n\n{text}"},
    ]
    body = {
        "messages": _messages_to_genapi(messages),
        "is_sync": True,
        "temperature": 0.3,
        "max_tokens": 1024,
    }

    logger.debug("Запрос к GenAPI для извлечения промпта дизайна")
    response = requests.post(url, headers=headers, json=body, timeout=120)
    response.raise_for_status()
    data = response.json()

    content = None
    if data.get("status") == "success" or "response" in data or "output" in data or "result" in data:
        try:
            content = _extract_text_from_genapi_output(data)
        except ValueError:
            pass

    request_id = data.get("request_id")
    if not content and request_id is not None:
        time.sleep(2)
        for _ in range(60):
            poll_url = f"{base_url}/request/get/{request_id}"
            poll_resp = requests.get(poll_url, headers=headers, timeout=30)
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()
            if poll_data.get("status") == "success":
                content = _extract_text_from_genapi_output(poll_data)
                break
            if poll_data.get("status") in ("failed", "error"):
                raise RuntimeError(f"GenAPI ошибка: {poll_data}")
            time.sleep(2)
        else:
            raise TimeoutError("GenAPI: превышено время ожидания")

    if not content:
        raise RuntimeError(f"Неожиданный ответ GenAPI: {data}")

    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(line for line in lines if not line.strip().startswith("```"))

    result = json.loads(content)
    logger.info("Извлечён промпт для генерации изображения")
    return result


def _extract_image_url_or_base64(data: dict) -> str | bytes | None:
    """Извлекает URL или base64 изображения из ответа GenAPI."""
    for key in ("output", "result", "full_response", "response"):
        val = data.get(key)
        if val is None:
            continue
        if isinstance(val, str) and (val.startswith("http") or val.startswith("data:")):
            return val
        if isinstance(val, list) and val:
            item = val[0]
            if isinstance(item, dict):
                url = item.get("url") or item.get("image_url")
                if url:
                    return url
                b64 = item.get("b64_json") or item.get("base64")
                if b64:
                    return base64.b64decode(b64)
            elif isinstance(item, str) and item.startswith("http"):
                return item
        if isinstance(val, dict):
            url = val.get("url") or val.get("image_url")
            if url:
                return url
    return None


def generate_design_image(prompt: str) -> str:
    """
    Генерирует изображение через GenAPI Flux. Возвращает путь к сохранённому файлу.
    """
    api_key = os.getenv("GENAPI_API_KEY") or os.getenv("GENAPI_KEY")
    if not api_key:
        raise ValueError("GENAPI_API_KEY требуется для генерации изображения")

    base_url = (os.getenv("GENAPI_BASE_URL") or "https://api.gen-api.ru/api/v1").rstrip("/")
    if "openai" in base_url.lower():
        base_url = "https://api.gen-api.ru/api/v1"
    image_model = os.getenv("GENAPI_IMAGE_MODEL", "flux")

    url = f"{base_url}/networks/{image_model}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "prompt": prompt[:4000],
        "is_sync": True,
        "translate_input": True,
        "width": 2048,
        "height": 2048,
    }

    logger.info("Генерация изображения через GenAPI (модель=%s)", image_model)
    response = requests.post(url, headers=headers, json=body, timeout=180)
    response.raise_for_status()
    data = response.json()

    img_data = None
    if data.get("status") == "success" or "response" in data or "output" in data or "result" in data:
        img_data = _extract_image_url_or_base64(data)

    request_id = data.get("request_id")
    if not img_data and request_id is not None:
        time.sleep(3)
        for _ in range(90):
            poll_url = f"{base_url}/request/get/{request_id}"
            poll_resp = requests.get(poll_url, headers=headers, timeout=60)
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()
            if poll_data.get("status") == "success":
                img_data = _extract_image_url_or_base64(poll_data)
                break
            if poll_data.get("status") in ("failed", "error"):
                raise RuntimeError(f"GenAPI ошибка генерации изображения: {poll_data}")
            time.sleep(2)
        else:
            raise TimeoutError("GenAPI: превышено время ожидания изображения")

    if not img_data:
        raise RuntimeError(f"Не удалось извлечь изображение из ответа: {list(data.keys())}")

    ext = ".png"
    if isinstance(img_data, str) and img_data.startswith("http"):
        img_resp = requests.get(img_data, timeout=60)
        img_resp.raise_for_status()
        img_bytes = img_resp.content
    elif isinstance(img_data, bytes):
        img_bytes = img_data
    else:
        raise RuntimeError("Неверный формат данных изображения")

    out_path = Path(tempfile.gettempdir()) / f"design_{os.getpid()}_{id(prompt)}{ext}"
    out_path.write_bytes(img_bytes)
    logger.info("Изображение сохранено: %s", out_path)
    return str(out_path)
