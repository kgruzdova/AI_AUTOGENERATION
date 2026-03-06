"""
Модуль генерации карточек товара для маркетплейса.
Из названия и стоимости получает описание и промпт для изображения (gpt-4o-mini),
генерирует изображение через gpt-image-1.
"""
import base64
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from utils.ai_processor import _extract_text_from_genapi_output, _messages_to_genapi

load_dotenv()

logger = logging.getLogger("report_generator.product_processor")

PRODUCT_SYSTEM_PROMPT = """Ты — помощник для создания карточек товара маркетплейса. По названию и стоимости товара создай данные для карточки.

Входные данные: название товара и стоимость.
Ответь СТРОГО в формате JSON без дополнительного текста:
{
    "name": "Название товара (краткое, для карточки)",
    "price": "Цена в формате для отображения (например: 599 ₽)",
    "description": "Краткое описание товара 1-2 предложения для карточки",
    "image_prompt": "Промпт на АНГЛИЙСКОМ для генерации фото товара: реалистичное изображение товара на нейтральном фоне, продающее, качественное. 1-2 предложения."
}"""


def _fallback_product_data(text: str) -> dict:
    """Фолбэк при пустом/некорректном ответе GenAPI: парсим название и цену из текста."""
    parts = re.split(r"\s*\|\s*", text.strip(), maxsplit=1)
    name = parts[0].strip() if parts else "Товар"
    price = parts[1].strip() if len(parts) > 1 else "—"
    return {
        "name": name,
        "price": price,
        "description": name,
        "image_prompt": f"Professional product photography of {name}, white background, studio lighting, high quality",
    }


def process_product_card(text: str) -> dict:
    """
    По названию и стоимости товара возвращает name, price, description, image_prompt.
    """
    if not os.getenv("GENAPI_API_KEY") and not os.getenv("GENAPI_KEY"):
        raise ValueError("GENAPI_API_KEY требуется для карточки товара")

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
        {"role": "system", "content": PRODUCT_SYSTEM_PROMPT},
        {"role": "user", "content": f"Товар: {text}"},
    ]
    body = {
        "messages": _messages_to_genapi(messages),
        "is_sync": True,
        "temperature": 0.3,
        "max_tokens": 512,
    }

    logger.debug("Запрос к GenAPI для карточки товара")
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

    if not content or not content.strip():
        logger.warning("GenAPI вернул пустой ответ, используем fallback из входных данных")
        return _fallback_product_data(text)

    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(line for line in lines if not line.strip().startswith("```"))
    content = content.strip()

    try:
        result = json.loads(content)
    except json.JSONDecodeError as e:
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(0))
            except json.JSONDecodeError:
                logger.warning("Некорректный JSON от GenAPI, fallback: %s", e)
                return _fallback_product_data(text)
        else:
            logger.warning("JSON не найден в ответе, fallback")
            return _fallback_product_data(text)

    for key in ("name", "price", "description", "image_prompt"):
        if key not in result:
            result[key] = ""
    logger.info("Извлечены данные карточки: name=%s", result.get("name", "—"))
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


def generate_product_image(prompt: str) -> str:
    """
    Генерирует изображение через GenAPI gpt-image-1. Возвращает путь к сохранённому файлу.
    """
    api_key = os.getenv("GENAPI_API_KEY") or os.getenv("GENAPI_KEY")
    if not api_key:
        raise ValueError("GENAPI_API_KEY требуется для генерации изображения")

    base_url = (os.getenv("GENAPI_BASE_URL") or "https://api.gen-api.ru/api/v1").rstrip("/")
    if "openai" in base_url.lower():
        base_url = "https://api.gen-api.ru/api/v1"
    image_model = os.getenv("GENAPI_PRODUCT_IMAGE_MODEL", "gpt-image-1")

    url = f"{base_url}/networks/{image_model}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "prompt": prompt[:4000],
        "is_sync": True,
        "size": "1024x1024",
    }

    logger.info("Генерация изображения через GenAPI (модель=%s)", image_model)
    response = requests.post(url, headers=headers, json=body, timeout=300)
    response.raise_for_status()
    data = response.json()

    img_data = None
    if data.get("status") == "success" or "response" in data or "output" in data or "result" in data:
        img_data = _extract_image_url_or_base64(data)

    request_id = data.get("request_id")
    if not img_data and request_id is not None:
        time.sleep(3)
        for _ in range(120):
            poll_url = f"{base_url}/request/get/{request_id}"
            poll_resp = requests.get(poll_url, headers=headers, timeout=60)
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()
            if poll_data.get("status") == "success":
                img_data = _extract_image_url_or_base64(poll_data)
                break
            if poll_data.get("status") in ("failed", "error"):
                raise RuntimeError(f"GenAPI ошибка генерации изображения: {poll_data}")
            time.sleep(3)
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

    out_path = Path(tempfile.gettempdir()) / f"product_{os.getpid()}_{id(prompt)}{ext}"
    out_path.write_bytes(img_bytes)
    logger.info("Изображение сохранено: %s", out_path)
    return str(out_path)
