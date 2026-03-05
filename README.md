# AI Client Report Generator

Автоматическая генерация PDF-отчётов с помощью ИИ. Три типа отчётов: клиентский, дизайн-отчёт, карточка товара. Доступ через CLI, Telegram-бот и Flask API.

## Установка

```bash
cd "AI Client Report Generator"
pip install -r requirements.txt
```

**Примечание:** PDF клиентского отчёта генерируется из HTML-шаблона (xhtml2pdf на Windows, WeasyPrint на Linux/macOS) с fallback на reportlab.

Создайте файл `.env` и добавьте API-ключ.

**GigaChat (рекомендуется для РФ):**
```env
GIGACHAT_CREDENTIALS=ваш_ключ
# GIGACHAT_VERIFY_SSL_CERTS=false  # для разработки без сертификата Госуслуг
```
Ключ: [developers.sber.ru](https://developers.sber.ru/studio) → GigaChat API.

**GenAPI / RockAPI / OpenAI-совместимые:**
```env
GENAPI_API_KEY=ваш_ключ
GENAPI_BASE_URL=https://api.gen-api.ru/api/v1
GENAPI_IMAGE_MODEL=flux   # для отчёта design
GENAPI_PRODUCT_IMAGE_MODEL=gpt-image-1  # для карточки товара
```
Провайдер выбирается автоматически (приоритет у GigaChat). Отчёт `design` требует GenAPI для LLM и генерации изображений (Flux).

## Использование

### Командная строка

При запуске появляется выбор типа отчёта:
```
1. Клиентский отчёт (стандартный)
2. Отчёт по дизайну сайта (с генерацией изображения)
3. Карточка товара для маркетплейса (название + стоимость)
```

**Варианты запуска:**
```bash
# Полностью интерактивный режим (выбор типа и источника)
python main.py

# Клиентский отчёт
python main.py sample_transcription.txt
python main.py sample_transcription.txt --type client

# Отчёт по дизайну (GenAPI Flux)
python main.py sample_transcription_design.txt --type design

# Карточка товара — интерактивно вводит название и стоимость
python main.py --type product
# Или передать данные:
python main.py "Кофе зерновой Арабика | 599 руб" --type product
```

Текст можно передать из файла или напрямую:
```bash
python main.py "Оператор: Здравствуйте. Клиент: Нужен дизайн лендинга..."
```

### Telegram-бот

```bash
python telegram_bot.py
```

Перед запуском добавьте в `.env`:
```env
TELEGRAM_BOT_TOKEN=ваш_токен  # от @BotFather
```

Бот предлагает выбрать тип отчёта, затем:
- **Клиентский / Дизайн** — отправить транскрибацию текстом или .txt файлом
- **Карточка товара** — ввести название и стоимость

### Flask API (опционально)

```bash
python app.py
```

Отправка запроса:

```bash
# Клиентский или дизайн (transcription обязателен)
curl -X POST http://localhost:5000/generate-report \
  -H "Content-Type: application/json" \
  -d '{"transcription": "Оператор: Здравствуйте. Клиент: ...", "report_type": "client"}'

# Карточка товара
curl -X POST http://localhost:5000/generate-report \
  -H "Content-Type: application/json" \
  -d '{"transcription": "Кофе Арабика | 599 руб", "report_type": "product"}'
```

## Структура проекта

```
AI Client Report Generator/
├── main.py                  # CLI
├── telegram_bot.py          # Telegram-бот
├── app.py                   # Flask API
├── templates/
│   └── report_template.html
├── reports/                 # готовые PDF
├── utils/
│   ├── ai_processor.py      # GigaChat / GenAPI / клиентский отчёт
│   ├── design_processor.py  # GenAPI LLM + Flux / дизайн-отчёт
│   ├── product_processor.py # gpt-4o-mini + gpt-image-1 / карточка товара
│   └── pdf_generator.py     # генерация PDF
├── sample_transcription.txt
├── sample_transcription_design.txt
├── sample_product.txt
├── .env                     # создать из .env.example
└── requirements.txt
```

## Формат данных отчёта

**Отчёт client:** ИИ извлекает client_name, topic, main_request, deadlines_and_cost, main_wishes, mood, next_steps.

**Отчёт design:** извлекается image_prompt, генерируется изображение (Flux), включается в PDF.

**Отчёт product:** по названию и стоимости товара ИИ формирует описание и промпт; генерируется изображение (gpt-image-1); PDF — карточка с изображением на весь фон и блоком (название, цена, описание) внизу.
