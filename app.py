"""
Опциональный Flask API для генерации отчётов по HTTP-запросам.
Запуск: python app.py
"""
import logging

from flask import Flask, request, jsonify

from utils.ai_processor import process_dialog_with_ai
from utils.design_processor import generate_design_image, process_design_dialog
from utils.pdf_generator import generate_design_report_pdf, generate_product_card_pdf, generate_report_pdf
from utils.product_processor import generate_product_image, process_product_card

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("report_generator.api")

app = Flask(__name__)


@app.route("/generate-report", methods=["POST"])
def generate_report():
    """
    POST /generate-report
    Body: {
        "transcription": "текст диалога или для product: название товара | стоимость",
        "report_type": "client" | "design" | "product"  (опционально)
    }
    """
    data = request.get_json()
    if not data or "transcription" not in data:
        logger.warning("Запрос без поля 'transcription'")
        return jsonify({"error": "Требуется поле 'transcription' в JSON"}), 400

    transcription = data["transcription"]
    report_type = (data.get("report_type") or "client")
    if report_type not in ("client", "design", "product"):
        report_type = "client"
    logger.info("Получен запрос на генерацию отчёта (тип=%s, длина=%d)", report_type, len(transcription))

    try:
        if report_type == "product":
            ai_data = process_product_card(transcription)
            prompt = ai_data.get("image_prompt", "")
            if not prompt:
                return jsonify({"success": False, "error": "Не получен промпт для изображения"}), 500
            image_path = generate_product_image(prompt)
            pdf_path = generate_product_card_pdf(ai_data, image_path)
        elif report_type == "design":
            ai_data = process_design_dialog(transcription)
            prompt = ai_data.get("image_prompt", "")
            image_path = None
            if prompt:
                try:
                    image_path = generate_design_image(prompt)
                except Exception as img_err:
                    logger.warning("Не удалось сгенерировать изображение: %s", img_err)
            pdf_path = generate_design_report_pdf(ai_data, image_path)
        else:
            ai_data = process_dialog_with_ai(transcription)
            pdf_path = generate_report_pdf(ai_data)
        logger.info("Отчёт успешно сгенерирован: %s", pdf_path)
        return jsonify({
            "success": True,
            "pdf_path": pdf_path,
            "report_type": report_type,
            "data": ai_data
        })
    except Exception as e:
        logger.exception("Ошибка при генерации отчёта: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
