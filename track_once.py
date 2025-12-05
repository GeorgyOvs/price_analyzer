import argparse
import os
import re
import socket
import sqlite3
import subprocess
import time
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import easyocr
from playwright.sync_api import sync_playwright
from selenium import webdriver
from selenium.webdriver.chrome.options import Options


CURRENCY_TOKENS = {
    "₽": "RUB",
    "руб": "RUB",
    "rub": "RUB",
    "р.": "RUB",
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
}

_reader: Optional[easyocr.Reader] = None


def get_ocr_reader() -> easyocr.Reader:
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["en", "ru"], gpu=False, verbose=False)
    return _reader


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            price_value REAL NOT NULL,
            currency TEXT NOT NULL,
            screenshot_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
        """
    )
    conn.commit()
    return conn


def get_or_create_product(conn: sqlite3.Connection, url: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM products WHERE url = ?", (url,))
    row = cur.fetchone()
    if row is not None:
        return row[0]

    created_at = datetime.utcnow().isoformat(timespec="seconds")
    cur.execute(
        "INSERT INTO products (url, created_at) VALUES (?, ?)",
        (url, created_at),
    )
    conn.commit()
    return cur.lastrowid


def build_screenshot_path(screenshots_dir: str, url: str) -> str:
    Path(screenshots_dir).mkdir(parents=True, exist_ok=True)
    url_hash = sha1(url.encode("utf-8")).hexdigest()[:10]
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{url_hash}_{timestamp}.png"
    return str(Path(screenshots_dir) / filename)


def take_screenshot(
    url: str,
    output_path: str,
    timeout_ms: int = 55000,
    headless: bool = True,
    debug_port: int = 9222,
    wait_after_ms: int = 3000,
) -> None:
    try:
        chrome_options = Options()
        chrome_options.add_experimental_option(
            "debuggerAddress", f"127.0.0.1:{debug_port}"
        )
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_window_size(1280, 720)
        driver.set_page_load_timeout(timeout_ms / 1000)
        driver.get(url)
        time.sleep(wait_after_ms / 1000)
        driver.save_screenshot(output_path)
    except Exception as e:
        print(f"[ERROR] Ошибка при создании скриншота: {e}")


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_chrome_debug_running(
    chrome_path: str,
    user_data_dir: str,
    debug_port: int,
    wait_seconds: int = 30,
) -> None:
    if is_port_open(debug_port):
        return
    args = [
        chrome_path,
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={user_data_dir}",
    ]
    try:
        subprocess.Popen(args)
    except FileNotFoundError:
        raise RuntimeError(f"Chrome не найден по пути: {chrome_path}")
    start_time = time.time()
    while time.time() - start_time < wait_seconds:
        if is_port_open(debug_port):
            return
        time.sleep(1)
    raise RuntimeError(
        "Не удалось запустить Chrome с debug портом за отведённое время"
    )


def run_ocr(image_path: str) -> List[Tuple[List[List[float]], str, float]]:
    reader = get_ocr_reader()
    # EasyOCR result format: [ [bbox, text, confidence], ... ]
    results = reader.readtext(image_path, detail=1)
    return results


def detect_currency(text: str) -> Optional[str]:
    lower = text.lower()
    for token, code in CURRENCY_TOKENS.items():
        if token in lower:
            return code
    return None


def parse_number(num_str: str) -> Optional[float]:
    s = num_str.replace("\xa0", "").replace(" ", "")

    if "," in s and "." in s:
        last_comma = s.rfind(",")
        last_dot = s.rfind(".")
        if last_comma > last_dot:
            decimal_sep = ","
        else:
            decimal_sep = "."
        other = "," if decimal_sep == "." else "."
        s = s.replace(other, "")
        s = s.replace(decimal_sep, ".")
    elif "," in s and "." not in s:
        if re.search(r",\d{1,2}$", s):
            s = s.replace(".", "")
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")

    try:
        return float(s)
    except ValueError:
        return None


def bbox_area(bbox: List[List[float]]) -> float:
    if not bbox:
        return 0.0
    xs = [pt[0] for pt in bbox]
    ys = [pt[1] for pt in bbox]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def choose_best_price_candidate(
    ocr_results: List[Tuple[List[List[float]], str, float]]
) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None

    for bbox, text, confidence in ocr_results:
        if not text:
            continue
        if not re.search(r"\d", text):
            continue

        currency = detect_currency(text)
        if currency is None:
            # Для MVP игнорируем числа без валюты
            continue

        match = re.search(r"(\d[\d\s.,]*)", text)
        if not match:
            continue

        value = parse_number(match.group(1))
        if value is None:
            continue

        if value < 1 or value > 1e9:
            continue

        area = bbox_area(bbox)
        score = area * float(confidence)

        candidate = {
            "text": text,
            "value": value,
            "currency": currency,
            "bbox": bbox,
            "confidence": float(confidence),
            "score": score,
        }

        if best is None or candidate["score"] > best["score"]:
            best = candidate

    return best


def save_price(
    conn: sqlite3.Connection,
    product_id: int,
    price_value: float,
    currency: str,
    screenshot_path: str,
) -> int:
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO prices (product_id, price_value, currency, screenshot_path, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (product_id, price_value, currency, screenshot_path, created_at),
    )
    conn.commit()
    return cur.lastrowid


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Однократная проверка цены по URL: скриншот → OCR → поиск цены → запись в SQLite."
        )
    )
    parser.add_argument("url", help="URL страницы товара")
    parser.add_argument(
        "--db-path",
        default="price_data.db",
        help="Путь к SQLite БД (по умолчанию price_data.db)",
    )
    parser.add_argument(
        "--screenshots-dir",
        default="screenshots",
        help="Каталог для сохранения скриншотов (по умолчанию ./screenshots)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=15000,
        help="Таймаут загрузки страницы в миллисекундах (по умолчанию 15000)",
    )
    parser.add_argument(
        "--wait-after-load-ms",
        type=int,
        default=3000,
        help=(
            "Пауза после загрузки страницы перед скриншотом в миллисекундах "
            "(по умолчанию 3000)"
        ),
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Открывать браузер в видимом режиме (не headless)",
    )
    parser.add_argument(
        "--debug-port",
        type=int,
        default=9222,
        help="Порт debug-соединения с уже запущенным Chrome (по умолчанию 9222)",
    )
    parser.add_argument(
        "--chrome-path",
        default=r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        help="Путь к chrome.exe для автоматического запуска",
    )
    parser.add_argument(
        "--chrome-user-data-dir",
        default="chrome_debug_profile",
        help="Каталог профиля Chrome для debug-сессии",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    url = args.url

    db_path = os.path.abspath(args.db_path)
    screenshots_dir = os.path.abspath(args.screenshots_dir)
    chrome_user_data_dir = os.path.abspath(args.chrome_user_data_dir)

    print(f"[INFO] URL: {url}")
    print(f"[INFO] БД: {db_path}")
    print(f"[INFO] Каталог скриншотов: {screenshots_dir}")

    conn = init_db(db_path)
    product_id = get_or_create_product(conn, url)

    ensure_chrome_debug_running(
        chrome_path=args.chrome_path,
        user_data_dir=chrome_user_data_dir,
        debug_port=args.debug_port,
    )

    screenshot_path = build_screenshot_path(screenshots_dir, url)
    print(f"[INFO] Делаю скриншот в {screenshot_path} ...")
    headless = not args.show_browser
    take_screenshot(
        url,
        screenshot_path,
        timeout_ms=args.timeout_ms,
        headless=headless,
        debug_port=args.debug_port,
        wait_after_ms=args.wait_after_load_ms,
    )

    print("[INFO] Запускаю OCR ...")
    ocr_results = run_ocr(screenshot_path)

    print(f"[INFO] Получено {len(ocr_results)} OCR-элементов")
    best = choose_best_price_candidate(ocr_results)

    if best is None:
        print("[WARN] Не удалось найти цену на скриншоте.")
        return

    price_id = save_price(
        conn,
        product_id,
        best["value"],
        best["currency"],
        screenshot_path,
    )

    print("[OK] Цена найдена и сохранена:")
    print(f"      URL: {url}")
    print(f"      Цена: {best['value']} {best['currency']}")
    print(f"      Текст OCR: {best['text']}")
    print(f"      Скриншот: {screenshot_path}")
    print(f"      Запись в БД (prices.id): {price_id}")


if __name__ == "__main__":
    main()
