# price_analyzer_v1

Простой скрипт для однократной проверки цены товара по URL:

1. Открывает страницу товара в Chrome через Selenium (debug port).
2. Делает скриншот страницы.
3. Распознаёт текст на скриншоте с помощью EasyOCR.
4. Пытается найти цену и валюту.
5. Сохраняет результат в SQLite-базу.

## Требования

- Python 3.9+
- Google Chrome
- ChromeDriver, совместимый с версией Chrome

## Установка

Создайте и активируйте виртуальное окружение, затем установите зависимости:

```bash
pip install -r requirements.txt
```

## Запуск

Базовый пример:

```bash
python track_once.py "https://example.com/product-page"
```

С основными параметрами:

```bash
python track_once.py "https://example.com/product-page" \
  --db-path price_data.db \
  --screenshots-dir screenshots \
  --timeout-ms 30000 \
  --wait-after-load-ms 8000 \
  --debug-port 9222 \
  --chrome-path "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" \
  --chrome-user-data-dir "chrome_debug_profile"
```

### Описание параметров

- `url` — URL страницы товара (обязательный позиционный аргумент).
- `--db-path` — путь к SQLite базе (по умолчанию `price_data.db`).
- `--screenshots-dir` — каталог для сохранения скриншотов (по умолчанию `screenshots`).
- `--timeout-ms` — таймаут загрузки страницы в миллисекундах.
- `--wait-after-load-ms` — пауза после загрузки страницы перед скриншотом.
- `--debug-port` — порт debug-соединения для Chrome.
- `--chrome-path` — путь к `chrome.exe` для авто-запуска.
- `--chrome-user-data-dir` — каталог профиля Chrome, где сохраняются куки/сессии.

## Принцип работы

- Скрипт проверяет, запущен ли Chrome с указанным debug-портом.
- Если не запущен — стартует Chrome с нужным профилем и debug-портом.
- Подключается к нему через Selenium и делает скриншот страницы.
- Запускает OCR по скриншоту и пытается найти цену и валюту.
- Сохраняет результат в таблицу `prices` SQLite-базы.

## Замечания

- При первом запуске с новым профилем (`--chrome-user-data-dir`) откроется новое окно Chrome. В нём можно залогиниться на нужные сайты, пройти капчи и т.п. После этого сессия будет использоваться автоматически.
- Для работы EasyOCR требуется корректная установка зависимостей (в том числе PyTorch). Если возникают проблемы с установкой, смотрите документацию EasyOCR.
