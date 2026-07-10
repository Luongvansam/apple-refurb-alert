import hashlib
import html
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from typing import Dict
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

APPLE_URL = os.getenv(
    "APPLE_URL",
    "https://www.apple.com/jp/shop/refurbished/iphone",
)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
CHECK_INTERVAL = max(30, int(os.getenv("CHECK_INTERVAL", "60")))
KEYWORDS = [x.strip().lower() for x in os.getenv("KEYWORDS", "").split(",") if x.strip()]
SEND_STARTUP_MESSAGE = os.getenv("SEND_STARTUP_MESSAGE", "true").lower() in {
    "1", "true", "yes", "on"
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("apple-refurb-alert")
stop_requested = False


@dataclass(frozen=True)
class Product:
    key: str
    name: str
    price: str
    url: str


def request_stop(*_args) -> None:
    global stop_requested
    stop_requested = True
    logger.info("Đang dừng bot...")


signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)


def validate_config() -> None:
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID")
    if missing:
        raise RuntimeError("Thiếu biến Railway: " + ", ".join(missing))


def telegram_send(text: str) -> None:
    endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(
        endpoint,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram trả lỗi: {payload}")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def matches_keywords(name: str) -> bool:
    if not KEYWORDS:
        return True
    lowered = name.lower()
    return any(keyword in lowered for keyword in KEYWORDS)


def extract_products(page_html: str) -> Dict[str, Product]:
    soup = BeautifulSoup(page_html, "html.parser")
    products: Dict[str, Product] = {}

    # Apple thường đặt sản phẩm trong các thẻ liên kết dẫn đến /shop/product/...
    for link in soup.select('a[href*="/shop/product/"]'):
        href = link.get("href", "").strip()
        if not href:
            continue

        container = link
        for _ in range(5):
            if container.parent is None:
                break
            container = container.parent
            container_text = normalize_text(container.get_text(" ", strip=True))
            if "円" in container_text and "iPhone" in container_text:
                break

        name = normalize_text(link.get_text(" ", strip=True))
        container_text = normalize_text(container.get_text(" ", strip=True))

        if not name or "iPhone" not in name:
            # Lấy tiêu đề từ khu vực sản phẩm nếu nội dung link chỉ là ảnh.
            heading = container.find(["h2", "h3", "h4"])
            if heading:
                name = normalize_text(heading.get_text(" ", strip=True))

        if not name or "iPhone" not in name or not matches_keywords(name):
            continue

        price_match = re.search(r"[\d,]+円", container_text)
        price = price_match.group(0) if price_match else "Không rõ giá"
        absolute_url = urljoin("https://www.apple.com", href)
        key = absolute_url.split("?")[0]

        products[key] = Product(
            key=key,
            name=name,
            price=price,
            url=absolute_url,
        )

    # Phương án dự phòng nếu Apple thay đổi HTML nhưng trang vẫn có tiêu đề sản phẩm.
    if not products:
        for heading in soup.find_all(["h2", "h3", "h4"]):
            name = normalize_text(heading.get_text(" ", strip=True))
            if "iPhone" not in name or not matches_keywords(name):
                continue
            link = heading.find("a", href=True) or heading.parent.find("a", href=True)
            if not link:
                continue
            href = link.get("href", "")
            absolute_url = urljoin("https://www.apple.com", href)
            nearby = normalize_text(heading.parent.get_text(" ", strip=True))
            price_match = re.search(r"[\d,]+円", nearby)
            price = price_match.group(0) if price_match else "Không rõ giá"
            key = absolute_url.split("?")[0]
            products[key] = Product(key, name, price, absolute_url)

    return products


def fetch_products() -> Dict[str, Product]:
    response = requests.get(
        APPLE_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.7",
            "Cache-Control": "no-cache",
        },
        timeout=30,
    )
    response.raise_for_status()
    products = extract_products(response.text)
    if not products:
        raise RuntimeError(
            "Không đọc được sản phẩm nào. Apple có thể đã đổi cấu trúc trang hoặc chặn yêu cầu."
        )
    return products


def product_message(product: Product) -> str:
    return (
        "🚨 <b>APPLE REFURBISHED CÓ HÀNG MỚI</b>\n\n"
        f"📱 <b>{html.escape(product.name)}</b>\n"
        f"💴 {html.escape(product.price)}\n\n"
        f'👉 <a href="{html.escape(product.url, quote=True)}">MỞ TRANG MUA NGAY</a>'
    )


def main() -> None:
    validate_config()

    if SEND_STARTUP_MESSAGE:
        keyword_text = ", ".join(KEYWORDS) if KEYWORDS else "tất cả iPhone"
        telegram_send(
            "✅ <b>Apple Stock Alert đã chạy</b>\n\n"
            f"Đang theo dõi: {html.escape(keyword_text)}\n"
            f"Chu kỳ kiểm tra: {CHECK_INTERVAL} giây\n\n"
            "Lần chạy đầu chỉ ghi nhận hàng hiện có, không gửi hàng loạt."
        )

    previous: Dict[str, Product] | None = None
    consecutive_errors = 0

    while not stop_requested:
        try:
            current = fetch_products()
            logger.info("Đã đọc %d sản phẩm.", len(current))

            if previous is None:
                previous = current
                logger.info("Đã tạo mốc ban đầu, không gửi cảnh báo.")
            else:
                new_keys = current.keys() - previous.keys()
                for key in sorted(new_keys):
                    product = current[key]
                    telegram_send(product_message(product))
                    logger.info("Đã báo sản phẩm mới: %s", product.name)
                    time.sleep(1)

                previous = current

            consecutive_errors = 0

        except Exception as exc:
            consecutive_errors += 1
            logger.exception("Lỗi kiểm tra lần %d: %s", consecutive_errors, exc)

            # Chỉ báo Telegram sau nhiều lỗi liên tiếp để tránh spam.
            if consecutive_errors in {5, 20}:
                try:
                    telegram_send(
                        "⚠️ <b>Apple Stock Alert đang gặp lỗi</b>\n\n"
                        f"{html.escape(str(exc))}\n\n"
                        "Bot sẽ tự tiếp tục thử lại."
                    )
                except Exception:
                    logger.exception("Không gửi được thông báo lỗi lên Telegram.")

        slept = 0
        while slept < CHECK_INTERVAL and not stop_requested:
            time.sleep(1)
            slept += 1

    logger.info("Bot đã dừng an toàn.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("Bot không thể khởi động: %s", exc)
        sys.exit(1)
