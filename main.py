import hashlib
import html
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


APPLE_URL = os.getenv(
    "APPLE_URL",
    "https://www.apple.com/jp/shop/refurbished/iphone",
)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

APPLE_INTERVAL = max(10, int(os.getenv("CHECK_INTERVAL", "10")))
APPLE_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv("KEYWORDS", "").split(",")
    if x.strip()
]

RAKUTEN_APP_ID = os.getenv("RAKUTEN_APP_ID", "").strip()
RAKUTEN_ACCESS_KEY = os.getenv("RAKUTEN_ACCESS_KEY", "").strip()
RAKUTEN_AFFILIATE_ID = os.getenv("RAKUTEN_AFFILIATE_ID", "").strip()
RAKUTEN_INTERVAL = max(30, int(os.getenv("RAKUTEN_INTERVAL", "60")))
RAKUTEN_KEYWORDS = [
    x.strip()
    for x in os.getenv(
        "RAKUTEN_KEYWORDS",
        "ストームエメラルダ,ポケモンカード BOX,ワンピースカード BOX",
    ).split(",")
    if x.strip()
]
RAKUTEN_MAX_PRICE = int(os.getenv("RAKUTEN_MAX_PRICE", "30000"))
RAKUTEN_HITS = min(30, max(1, int(os.getenv("RAKUTEN_HITS", "30"))))

SEND_STARTUP_MESSAGE = os.getenv("SEND_STARTUP_MESSAGE", "true").lower() in {
    "1", "true", "yes", "on"
}

RAKUTEN_ENDPOINT = (
    "https://openapi.rakuten.co.jp/ichibams/api/"
    "IchibaItem/Search/20260701"
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("apple-rakuten-alert")

stop_requested = False


@dataclass(frozen=True)
class Product:
    key: str
    name: str
    price: str
    url: str
    shop: str = ""
    source: str = ""


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


def matches_apple_keywords(name: str) -> bool:
    if not APPLE_KEYWORDS:
        return True
    lowered = name.lower()
    return any(keyword in lowered for keyword in APPLE_KEYWORDS)


def extract_apple_products(page_html: str) -> Dict[str, Product]:
    soup = BeautifulSoup(page_html, "html.parser")
    products: Dict[str, Product] = {}

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
            heading = container.find(["h2", "h3", "h4"])
            if heading:
                name = normalize_text(heading.get_text(" ", strip=True))

        if not name or "iPhone" not in name or not matches_apple_keywords(name):
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
            source="Apple",
        )

    if not products:
        for heading in soup.find_all(["h2", "h3", "h4"]):
            name = normalize_text(heading.get_text(" ", strip=True))
            if "iPhone" not in name or not matches_apple_keywords(name):
                continue

            link = heading.find("a", href=True)
            if not link and heading.parent:
                link = heading.parent.find("a", href=True)
            if not link:
                continue

            href = link.get("href", "")
            absolute_url = urljoin("https://www.apple.com", href)
            nearby = normalize_text(heading.parent.get_text(" ", strip=True))
            price_match = re.search(r"[\d,]+円", nearby)
            price = price_match.group(0) if price_match else "Không rõ giá"
            key = absolute_url.split("?")[0]

            products[key] = Product(
                key=key,
                name=name,
                price=price,
                url=absolute_url,
                source="Apple",
            )

    return products


def fetch_apple_products() -> Dict[str, Product]:
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

    products = extract_apple_products(response.text)
    if not products:
        raise RuntimeError(
            "Không đọc được sản phẩm Apple. "
            "Apple có thể đã đổi cấu trúc trang hoặc chặn yêu cầu."
        )
    return products


def rakuten_item_allowed(name: str) -> bool:
    lowered = normalize_text(name).lower()

    required = (
        "box" in lowered
        or "ボックス" in lowered
        or "30パック" in lowered
        or "24パック" in lowered
        or "未開封" in lowered
    )
    if not required:
        return False

    blocked_words = (
        "カード単品",
        "シングルカード",
        "オリパ",
        "福袋",
        "中古",
        "空箱",
        "箱のみ",
        "パック単品",
        "1パック",
        "バラ",
        "サプライ",
        "スリーブ",
        "デッキケース",
        "プレイマット",
    )
    return not any(word in lowered for word in blocked_words)


def fetch_rakuten_query(keyword: str) -> Dict[str, Product]:
    if not RAKUTEN_APP_ID or not RAKUTEN_ACCESS_KEY:
        return {}

    params = {
        "applicationId": RAKUTEN_APP_ID,
        "accessKey": RAKUTEN_ACCESS_KEY,
        "keyword": keyword,
        "format": "json",
        "formatVersion": 2,
        "hits": RAKUTEN_HITS,
        "page": 1,
        "sort": "-updateTimestamp",
        "availability": 1,
        "field": 0,
        "imageFlag": 1,
        "maxPrice": RAKUTEN_MAX_PRICE,
        "elements": ",".join(
            [
                "itemName",
                "itemPrice",
                "itemUrl",
                "itemCode",
                "availability",
                "shopName",
            ]
        ),
    }
    if RAKUTEN_AFFILIATE_ID:
        params["affiliateId"] = RAKUTEN_AFFILIATE_ID

    response = requests.get(
        RAKUTEN_ENDPOINT,
        params=params,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    products: Dict[str, Product] = {}
    for item in payload.get("items", []):
        name = normalize_text(str(item.get("itemName", "")))
        if not name or not rakuten_item_allowed(name):
            continue

        item_code = str(item.get("itemCode", "")).strip()
        url = str(item.get("itemUrl", "")).strip()
        if not url:
            continue

        key_raw = item_code or url.split("?")[0]
        key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
        price_value = item.get("itemPrice")
        price = f"¥{int(price_value):,}" if isinstance(price_value, (int, float)) else "Không rõ giá"
        shop = normalize_text(str(item.get("shopName", "")))

        products[key] = Product(
            key=key,
            name=name,
            price=price,
            url=url,
            shop=shop,
            source=f"Rakuten: {keyword}",
        )

    return products


def fetch_all_rakuten_products() -> Dict[str, Product]:
    products: Dict[str, Product] = {}
    for index, keyword in enumerate(RAKUTEN_KEYWORDS):
        products.update(fetch_rakuten_query(keyword))
        if index < len(RAKUTEN_KEYWORDS) - 1:
            time.sleep(1)
    return products


def apple_message(product: Product) -> str:
    return (
        "🍎 <b>APPLE REFURBISHED CÓ HÀNG MỚI</b>\n\n"
        f"📱 {html.escape(product.name)}\n"
        f"💴 {html.escape(product.price)}\n\n"
        f'🔗 <a href="{html.escape(product.url, quote=True)}">'
        "MỞ TRANG MUA NGAY</a>"
    )


def rakuten_message(product: Product) -> str:
    shop_line = (
        f"🏪 {html.escape(product.shop)}\n"
        if product.shop
        else ""
    )
    return (
        "🟨 <b>RAKUTEN CÓ HÀNG MỚI</b>\n\n"
        f"🎴 {html.escape(product.name)}\n"
        f"{shop_line}"
        f"💴 {html.escape(product.price)}\n\n"
        f'🔗 <a href="{html.escape(product.url, quote=True)}">'
        "MỞ TRANG MUA NGAY</a>"
    )


def notify_new_products(
    previous: Dict[str, Product],
    current: Dict[str, Product],
    message_builder,
) -> None:
    new_keys = current.keys() - previous.keys()
    for key in sorted(new_keys):
        product = current[key]
        telegram_send(message_builder(product))
        logger.info("Đã báo sản phẩm mới: %s", product.name)
        time.sleep(1)


def interruptible_sleep(seconds: int) -> None:
    slept = 0
    while slept < seconds and not stop_requested:
        time.sleep(1)
        slept += 1


def main() -> None:
    validate_config()

    rakuten_enabled = bool(RAKUTEN_APP_ID and RAKUTEN_ACCESS_KEY)

    if SEND_STARTUP_MESSAGE:
        apple_text = ", ".join(APPLE_KEYWORDS) if APPLE_KEYWORDS else "tất cả iPhone"
        if rakuten_enabled:
            rakuten_text = ", ".join(RAKUTEN_KEYWORDS)
            rakuten_status = (
                f"✅ Rakuten: {html.escape(rakuten_text)}\n"
                f"Chu kỳ Rakuten: {RAKUTEN_INTERVAL} giây"
            )
        else:
            rakuten_status = (
                "⚠️ Rakuten chưa bật: thiếu RAKUTEN_APP_ID "
                "hoặc RAKUTEN_ACCESS_KEY"
            )

        telegram_send(
            "✅ <b>Apple + Rakuten Stock Alert đã chạy</b>\n\n"
            f"🍎 Apple: {html.escape(apple_text)}\n"
            f"Chu kỳ Apple: {APPLE_INTERVAL} giây\n\n"
            f"{rakuten_status}\n\n"
            "Lần chạy đầu chỉ ghi nhận hàng hiện có, không gửi hàng loạt."
        )

    apple_previous: Dict[str, Product] | None = None
    rakuten_previous: Dict[str, Product] | None = None

    last_apple_check = 0.0
    last_rakuten_check = 0.0
    apple_errors = 0
    rakuten_errors = 0

    while not stop_requested:
        now = time.monotonic()

        if now - last_apple_check >= APPLE_INTERVAL:
            last_apple_check = now
            try:
                current = fetch_apple_products()
                logger.info("Apple: đã đọc %d sản phẩm.", len(current))

                if apple_previous is None:
                    apple_previous = current
                    logger.info("Apple: đã tạo mốc ban đầu.")
                else:
                    notify_new_products(
                        apple_previous,
                        current,
                        apple_message,
                    )
                    apple_previous = current
                apple_errors = 0
            except Exception as exc:
                apple_errors += 1
                logger.exception("Apple lỗi lần %d: %s", apple_errors, exc)
                if apple_errors in {5, 20}:
                    try:
                        telegram_send(
                            "⚠️ <b>Apple Stock Alert đang gặp lỗi</b>\n\n"
                            f"{html.escape(str(exc))}\n\n"
                            "Bot sẽ tự tiếp tục thử lại."
                        )
                    except Exception:
                        logger.exception("Không gửi được lỗi Apple lên Telegram.")

        now = time.monotonic()
        if rakuten_enabled and now - last_rakuten_check >= RAKUTEN_INTERVAL:
            last_rakuten_check = now
            try:
                current = fetch_all_rakuten_products()
                logger.info("Rakuten: đã đọc %d sản phẩm.", len(current))

                if rakuten_previous is None:
                    rakuten_previous = current
                    logger.info("Rakuten: đã tạo mốc ban đầu.")
                else:
                    notify_new_products(
                        rakuten_previous,
                        current,
                        rakuten_message,
                    )
                    rakuten_previous = current
                rakuten_errors = 0
            except Exception as exc:
                rakuten_errors += 1
                logger.exception("Rakuten lỗi lần %d: %s", rakuten_errors, exc)
                if rakuten_errors in {3, 10}:
                    try:
                        telegram_send(
                            "⚠️ <b>Rakuten Stock Alert đang gặp lỗi</b>\n\n"
                            f"{html.escape(str(exc))}\n\n"
                            "Bot sẽ tự tiếp tục thử lại."
                        )
                    except Exception:
                        logger.exception("Không gửi được lỗi Rakuten lên Telegram.")

        interruptible_sleep(1)

    logger.info("Bot đã dừng an toàn.")


if _ _name_ _ == "_ _main_ _":
    try:
        main()
    except Exception as exc:
        logger.exception("Bot không thể khởi động: %s", exc)
        sys.exit(1)

