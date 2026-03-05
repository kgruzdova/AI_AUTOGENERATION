"""
AI Client Report Generator — генерация PDF-отчётов по диалогам с клиентами.

Типы отчётов:
  client  — отчёт по диалогу с клиентом (по умолчанию)
  design  — отчёт по заказу на дизайн сайта с примером изображения (GenAPI Flux)
  product — карточка товара для маркетплейса (название + стоимость → PDF с фоновым изображением)
"""
import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from utils.ai_processor import process_dialog_with_ai
from utils.design_processor import generate_design_image, process_design_dialog
from utils.pdf_generator import generate_design_report_pdf, generate_product_card_pdf, generate_report_pdf
from utils.product_processor import generate_product_image, process_product_card

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("report_generator")


def load_transcription(source: str) -> str:
    """
    Загружает транскрибацию из файла или возвращает переданный текст.
    """
    path = Path(source)
    if path.is_file():
        logger.info("Загрузка транскрибации из файла: %s", path)
        return path.read_text(encoding="utf-8")
    logger.debug("Транскрибация передана как текст (длина: %d символов)", len(source))
    return source


def main():
    parser = argparse.ArgumentParser(
        description="AI Client Report Generator — генерация PDF-отчётов по диалогам с клиентами",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python main.py transcription.txt
  python main.py transcription.txt --type client
  python main.py transcription.txt --type design
  python main.py "Кофе зерновой Арабика | 599 руб" --type product
        """,
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=None,
        help="Путь к файлу или текст (если не указан — будет запрошен)",
    )
    parser.add_argument(
        "--type",
        dest="report_type",
        choices=["client", "design", "product"],
        default=None,
        help="Тип отчёта: client, design, product (карточка товара)",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Не спрашивать тип отчёта, использовать client по умолчанию",
    )
    args = parser.parse_args()

    if args.report_type is None and not args.yes and sys.stdin.isatty():
        print("\nВыберите тип отчёта:")
        print("  1. Клиентский отчёт (стандартный)")
        print("  2. Отчёт по дизайну сайта (с генерацией изображения)")
        print("  3. Карточка товара для маркетплейса (название + стоимость)")
        try:
            choice = input("Ваш выбор [1]: ").strip() or "1"
            args.report_type = {"1": "client", "2": "design", "3": "product"}.get(choice, "client")
        except (EOFError, KeyboardInterrupt):
            args.report_type = "client"
            print("Используется: клиентский отчёт")
    if args.report_type is None:
        args.report_type = "client"

    transcription = None
    if args.report_type == "product":
        if sys.stdin.isatty():
            try:
                print("\nВведите данные товара:")
                name = input("  Название товара: ").strip()
                price = input("  Стоимость товара: ").strip()
                transcription = f"{name} | {price}" if name or price else None
            except (EOFError, KeyboardInterrupt):
                transcription = None
        else:
            transcription = args.source
        if not transcription or not transcription.strip():
            if args.source:
                transcription = load_transcription(args.source)
            if not transcription or not transcription.strip():
                print("Ошибка: укажите товар. Пример: python main.py \"Кофе Арабика | 599 руб\" --type product")
                sys.exit(1)
    else:
        if args.source is None:
            if sys.stdin.isatty():
                try:
                    args.source = input("Путь к файлу или текст [sample_transcription.txt]: ").strip() or "sample_transcription.txt"
                except (EOFError, KeyboardInterrupt):
                    args.source = "sample_transcription.txt"
                    print("Используется: sample_transcription.txt")
            else:
                print("Ошибка: укажите файл или текст. Пример: python main.py sample_transcription.txt")
                sys.exit(1)
        logger.info("Загрузка транскрибации...")
        transcription = load_transcription(args.source)
        if not transcription.strip():
            logger.error("Транскрибация пуста")
            sys.exit(1)

    if args.report_type == "design":
        logger.info("Анализ диалога по заказу дизайна (GenAPI LLM)...")
        try:
            data = process_design_dialog(transcription)
            logger.info("Извлечены данные: client_name=%s", data.get("client_name", "—"))
        except Exception as e:
            logger.exception("Ошибка при обработке диалога: %s", e)
            sys.exit(1)

        prompt = data.get("image_prompt", "")
        if not prompt:
            logger.warning("Не получен промпт для изображения — отчёт будет без примера")
            image_path = None
        else:
            logger.info("Генерация изображения (GenAPI Flux)...")
            try:
                image_path = generate_design_image(prompt)
            except Exception as e:
                logger.warning("Не удалось сгенерировать изображение: %s — отчёт будет без примера", e)
                image_path = None

        logger.info("Генерация PDF-отчёта (design)...")
        try:
            pdf_path = generate_design_report_pdf(data, image_path)
            logger.info("Отчёт успешно создан: %s", pdf_path)
        except Exception as e:
            logger.exception("Ошибка при генерации PDF: %s", e)
            sys.exit(1)
    elif args.report_type == "product":
        logger.info("Обработка карточки товара (gpt-4o-mini + gpt-image-1)...")
        try:
            data = process_product_card(transcription)
            logger.info("Извлечены данные: name=%s, price=%s", data.get("name", "—"), data.get("price", "—"))
        except Exception as e:
            logger.exception("Ошибка при обработке товара: %s", e)
            sys.exit(1)

        prompt = data.get("image_prompt", "")
        if not prompt:
            logger.error("Не получен промпт для изображения — карточка требует изображение")
            sys.exit(1)
        try:
            image_path = generate_product_image(prompt)
        except Exception as e:
            logger.exception("Ошибка генерации изображения: %s", e)
            sys.exit(1)

        logger.info("Генерация PDF карточки товара...")
        try:
            pdf_path = generate_product_card_pdf(data, image_path)
            logger.info("Карточка успешно создана: %s", pdf_path)
        except Exception as e:
            logger.exception("Ошибка при генерации PDF: %s", e)
            sys.exit(1)
    else:
        logger.info("Анализ диалога с помощью ИИ...")
        try:
            data = process_dialog_with_ai(transcription)
            logger.info("ИИ успешно извлёк данные: client_name=%s", data.get("client_name", "—"))
        except Exception as e:
            logger.exception("Ошибка при обработке ИИ: %s", e)
            sys.exit(1)

        logger.info("Генерация PDF-отчёта...")
        try:
            pdf_path = generate_report_pdf(data)
            logger.info("Отчёт успешно создан: %s", pdf_path)
        except Exception as e:
            logger.exception("Ошибка при генерации PDF: %s", e)
            sys.exit(1)


if __name__ == "__main__":
    main()
