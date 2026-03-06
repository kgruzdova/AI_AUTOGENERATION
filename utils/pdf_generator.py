"""
Модуль генерации PDF из данных отчёта.
Использует HTML-шаблон + WeasyPrint (при наличии) или xhtml2pdf с поддержкой кириллицы,
при необходимости — reportlab.
"""
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Image as RLImage, Paragraph, SimpleDocTemplate, Spacer

logger = logging.getLogger("report_generator.pdf_generator")


def _get_font_path() -> str | None:
    """Путь к шрифту с кириллицей. Копирует системный шрифт в fonts/, если нужно."""
    project_root = Path(__file__).resolve().parent.parent
    fonts_dir = project_root / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)

    if (fonts_dir / "DejaVuSans.ttf").exists():
        return str((fonts_dir / "DejaVuSans.ttf").resolve())

    arial_local = fonts_dir / "arial.ttf"
    if arial_local.exists():
        return str(arial_local.resolve())
    if sys.platform == "win32":
        arial_sys = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "arial.ttf"
        if arial_sys.exists():
            try:
                shutil.copy2(arial_sys, arial_local)
                return str(arial_local.resolve())
            except OSError as e:
                logger.debug("Не удалось скопировать Arial: %s", e)
                return str(arial_sys.resolve())
    return None


def _get_font_url_for_template(font_path: str | None, project_root: Path) -> tuple[str | None, Path]:
    """Возвращает (url для @font-face, base_path для pisa)."""
    if not font_path:
        return None, project_root
    p = Path(font_path).resolve()
    try:
        rel = p.relative_to(project_root.resolve())
        return str(rel).replace("\\", "/"), project_root
    except ValueError:
        return Path(font_path).as_uri(), project_root


def _generate_report_pdf_from_template(data: dict, output_path: Path, font_path: str | None) -> bool:
    """Генерирует PDF из HTML-шаблона (WeasyPrint или xhtml2pdf). Возвращает True при успехе."""
    project_root = Path(__file__).resolve().parent.parent
    templates_dir = project_root / "templates"
    if not (templates_dir / "report_template.html").exists():
        logger.debug("Шаблон report_template.html не найден")
        return False

    font_url, base_path = _get_font_url_for_template(font_path, project_root)

    next_steps = data.get("next_steps", "—")
    if isinstance(next_steps, list):
        next_steps = "\n".join(f"• {s}" for s in next_steps)

    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
    template = env.get_template("report_template.html")
    html_str = template.render(
        client_name=data.get("client_name", "Клиент"),
        report_date=datetime.now().strftime("%d.%m.%Y"),
        report_time=datetime.now().strftime("%H:%M"),
        topic=data.get("topic", "—"),
        main_request=data.get("main_request", "—"),
        deadlines_and_cost=data.get("deadlines_and_cost", "Не указано"),
        main_wishes=data.get("main_wishes", "Не указано"),
        mood=data.get("mood", "—"),
        next_steps=str(next_steps),
        font_url=font_url,
    )

    try:
        from weasyprint import HTML
        from weasyprint.fonts import FontConfiguration
        font_config = FontConfiguration()
        html_doc = HTML(string=html_str, base_url=str(base_path))
        html_doc.write_pdf(str(output_path), font_config=font_config)
        logger.info("PDF сгенерирован из шаблона (WeasyPrint): %s", output_path)
        return True
    except (ImportError, OSError, Exception) as e:
        logger.debug("WeasyPrint: %s", e)

    try:
        from xhtml2pdf import pisa
        with open(output_path, "wb") as dest_file:
            result = pisa.CreatePDF(
                html_str.encode("utf-8"),
                dest=dest_file,
                encoding="utf-8",
                path=str(base_path),
            )
        if not result.err:
            logger.info("PDF сгенерирован из шаблона (xhtml2pdf): %s", output_path)
            return True
        logger.debug("xhtml2pdf err: %s", result.err)
    except Exception as e:
        logger.debug("xhtml2pdf: %s", e)
    return False


def _register_cyrillic_font() -> str:
    """Регистрирует шрифт с кириллицей. Возвращает имя шрифта."""
    font_name = "ReportFont"
    try:
        project_root = Path(__file__).resolve().parent.parent
        fonts_dir = project_root / "fonts"
        if (fonts_dir / "DejaVuSans.ttf").exists():
            pdfmetrics.registerFont(TTFont(font_name, str(fonts_dir / "DejaVuSans.ttf")))
            return font_name
        if sys.platform == "win32":
            arial = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "arial.ttf"
            if arial.exists():
                pdfmetrics.registerFont(TTFont(font_name, str(arial)))
                return font_name
    except Exception as e:
        logger.warning("Не удалось загрузить шрифт с кириллицей: %s", e)
    return "Helvetica"


def generate_report_pdf(data: dict, output_dir: str = "reports") -> str:
    """
    Генерирует PDF-отчёт с данными. Сначала пробует HTML-шаблон (WeasyPrint/xhtml2pdf),
    при неудаче — reportlab. Кириллица: через Arial/DejaVu (reportlab) или @font-face (шаблон).
    """
    project_root = Path(__file__).resolve().parent.parent
    reports_path = project_root / output_dir
    reports_path.mkdir(parents=True, exist_ok=True)
    filename = f"report_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.pdf"
    output_path = reports_path / filename

    font_path = _get_font_path()
    if _generate_report_pdf_from_template(data, output_path, font_path):
        logger.info("PDF сгенерирован из шаблона: %s", output_path)
        return str(output_path)

    logger.info("Использование reportlab (шаблон недоступен)")
    font_name = _register_cyrillic_font()
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle",
        fontName=font_name,
        fontSize=14,
        textColor=colors.HexColor("#1e40af"),
        spaceAfter=20,
    ))
    styles.add(ParagraphStyle(
        name="ReportMeta",
        fontName=font_name,
        fontSize=9,
        textColor=colors.HexColor("#64748b"),
        spaceAfter=20,
    ))
    styles.add(ParagraphStyle(
        name="SectionTitle",
        fontName=font_name,
        fontSize=12,
        textColor=colors.HexColor("#1e40af"),
        spaceAfter=6,
        spaceBefore=12,
    ))
    styles.add(ParagraphStyle(
        name="SectionBody",
        fontName=font_name,
        fontSize=11,
        leading=16,
        textColor=colors.HexColor("#333333"),
        spaceAfter=6,
    ))

    def _para(text: str, style: str = "SectionBody") -> Paragraph:
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return Paragraph(safe, styles[style])

    def _escape(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    client = _escape(str(data.get("client_name", "Клиент")))
    meta_style = ParagraphStyle(
        name="ClientMeta",
        fontName=font_name,
        fontSize=11,
        textColor=colors.black,
        spaceAfter=20,
    )
    story = []
    story.append(_para("Отчёт по диалогу с клиентом", "ReportTitle"))
    meta = f"Клиент: {client} | Дата: {datetime.now().strftime('%d.%m.%Y')} | Время: {datetime.now().strftime('%H:%M')}"
    story.append(Paragraph(meta, meta_style))
    story.append(Spacer(1, 6))

    story.append(_para("Тема обращения", "SectionTitle"))
    story.append(_para(str(data.get("topic", "—"))))
    story.append(_para("Основной запрос", "SectionTitle"))
    story.append(_para(str(data.get("main_request", "—"))))
    story.append(_para("Желаемые сроки и стоимость", "SectionTitle"))
    story.append(_para(str(data.get("deadlines_and_cost", "Не указано"))))
    story.append(_para("Что точно должно быть в финальном продукте (основные пожелания)", "SectionTitle"))
    story.append(_para(str(data.get("main_wishes", "Не указано"))))
    story.append(_para("Настроение клиента", "SectionTitle"))
    story.append(_para(str(data.get("mood", "—"))))
    story.append(_para("Рекомендуемые следующие шаги", "SectionTitle"))
    next_steps = data.get("next_steps", "—")
    if isinstance(next_steps, list):
        next_steps = "\n".join(f"• {s}" for s in next_steps)
    story.append(_para(str(next_steps)))
    story.append(Spacer(1, 24))
    story.append(_para("Сформировано автоматически: AI Client Report Generator", "ReportMeta"))

    logger.info("Сохранение PDF: %s", output_path)
    doc.build(story)
    return str(output_path)


def generate_design_report_pdf(data: dict, image_path: str | None = None, output_dir: str = "reports") -> str:
    """
    Генерирует PDF-отчёт по заказу дизайна сайта с примером изображения.
    """
    font_name = _register_cyrillic_font()
    project_root = Path(__file__).resolve().parent.parent
    reports_path = project_root / output_dir
    reports_path.mkdir(parents=True, exist_ok=True)
    filename = f"report_design_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.pdf"
    output_path = reports_path / filename

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle",
        fontName=font_name,
        fontSize=14,
        textColor=colors.HexColor("#1e40af"),
        spaceAfter=20,
    ))
    styles.add(ParagraphStyle(
        name="ReportMeta",
        fontName=font_name,
        fontSize=9,
        textColor=colors.HexColor("#64748b"),
        spaceAfter=20,
    ))
    styles.add(ParagraphStyle(
        name="SectionTitle",
        fontName=font_name,
        fontSize=12,
        textColor=colors.HexColor("#1e40af"),
        spaceAfter=6,
        spaceBefore=12,
    ))
    styles.add(ParagraphStyle(
        name="SectionBody",
        fontName=font_name,
        fontSize=11,
        leading=16,
        textColor=colors.HexColor("#333333"),
        spaceAfter=6,
    ))

    def _para(text: str, style: str = "SectionBody") -> Paragraph:
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return Paragraph(safe, styles[style])

    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    client = _esc(str(data.get("client_name", "Клиент")))
    meta_style = ParagraphStyle(
        name="ClientMeta",
        fontName=font_name,
        fontSize=11,
        textColor=colors.black,
        spaceAfter=20,
    )
    story = []
    story.append(_para("Отчёт по заказу на дизайн сайта", "ReportTitle"))
    meta = f"Клиент: {client} | Дата: {datetime.now().strftime('%d.%m.%Y')} | Время: {datetime.now().strftime('%H:%M')}"
    story.append(Paragraph(meta, meta_style))
    story.append(Spacer(1, 6))

    story.append(_para("Тема заказа", "SectionTitle"))
    story.append(_para(str(data.get("topic", "—"))))
    story.append(_para("Основные требования к дизайну", "SectionTitle"))
    story.append(_para(str(data.get("main_request", "—"))))
    story.append(_para("Желаемые сроки и стоимость", "SectionTitle"))
    story.append(_para(str(data.get("deadlines_and_cost", "Не указано"))))
    story.append(_para("Основные пожелания", "SectionTitle"))
    story.append(_para(str(data.get("main_wishes", "Не указано"))))

    if image_path and Path(image_path).exists():
        story.append(_para("Пример дизайна (сгенерировано AI)", "SectionTitle"))
        img = RLImage(image_path, width=14 * cm, height=14 * cm)
        story.append(img)
        story.append(Spacer(1, 12))

    story.append(Spacer(1, 24))
    story.append(_para("Сформировано автоматически: AI Client Report Generator", "ReportMeta"))

    logger.info("Сохранение PDF дизайн-отчёта: %s", output_path)
    doc.build(story)
    return str(output_path)


def generate_product_card_pdf(data: dict, image_path: str, output_dir: str = "reports") -> str:
    """
    Генерирует PDF карточки товара: изображение на весь фон, поверх — название, цена, описание.
    """
    font_name = _register_cyrillic_font()
    project_root = Path(__file__).resolve().parent.parent
    reports_path = project_root / output_dir
    reports_path.mkdir(parents=True, exist_ok=True)
    filename = f"product_card_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.pdf"
    output_path = reports_path / filename

    w, h = A4
    c = canvas.Canvas(str(output_path), pagesize=A4)

    if image_path and Path(image_path).exists():
        from reportlab.lib.utils import ImageReader
        img = ImageReader(image_path)
        iw, ih = img.getSize()
        scale = max(w / iw, h / ih)
        nw, nh = iw * scale, ih * scale
        x, y = (w - nw) / 2, (h - nh) / 2
        c.drawImage(image_path, x, y, width=nw, height=nh)

    bar_height = 5 * cm
    c.setFillColor(colors.Color(0, 0, 0, 0.7))
    c.rect(0, 0, w, bar_height, fill=True, stroke=False)

    c.setFillColor(colors.white)
    c.setFont(font_name, 18)
    name = str(data.get("name", "Товар"))
    c.drawString(1.5 * cm, bar_height - 1.8 * cm, name[:60])

    c.setFont(font_name, 16)
    price = str(data.get("price", "—"))
    c.drawString(1.5 * cm, bar_height - 2.8 * cm, price[:40])

    c.setFont(font_name, 10)
    desc = str(data.get("description", ""))[:200]
    for i, line in enumerate(desc.split("\n")[:2]):
        c.drawString(1.5 * cm, bar_height - 3.6 * cm - i * 0.5 * cm, line[:80])

    c.save()
    logger.info("Карточка товара сохранена: %s", output_path)
    return str(output_path)
