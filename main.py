import hashlib
import html
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
CHECK_INTERVAL = max(10, int(os.getenv("CHECK_INTERVAL", "10")))
RAKUTEN_INTERVAL = max(30, int(os.getenv("RAKUTEN_INTERVAL", "30")))
YAHOO_INTERVAL = max(10, int(os.getenv("YAHOO_INTERVAL", "10")))
SEND_STARTUP_MESSAGE = os.getenv("SEND_STARTUP_MESSAGE", "true").lower() in {"1", "true", "yes", "on"}

APPLE_URL = os.getenv("APPLE_URL", "https://www.apple.com/jp/shop/refurbished/iphone").strip()
APPLE_KEYWORDS = [x.strip().lower() for x in os.getenv("KEYWORDS", "").split(",") if x.strip()]

RAKUTEN_APP_ID = os.getenv("RAKUTEN_APP_ID", "").strip()
RAKUTEN_ACCESS_KEY = os.getenv("RAKUTEN_ACCESS_KEY", "").strip()
RAKUTEN_AFFILIATE_ID = os.getenv("RAKUTEN_AFFILIATE_ID", "").strip()
RAKUTEN_KEYWORDS = [
    x.strip()
    for x in os.getenv(
        "RAKUTEN_KEYWORDS",
        "ストームエメラルダ,ポケモンカード BOX,ワンピースカード BOX,神の島の冒険 BOX",
    ).split(",")
    if x.strip()
]
RAKUTEN_MAX_PRICE = int(os.getenv("RAKUTEN_MAX_PRICE", "30000"))
RAKUTEN_HITS = min(30, max(1, int(os.getenv("RAKUTEN_HITS", "30"))))
RAKUTEN_ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
YAHOO_URLS = [
    x.strip()
    for x in os.getenv(
        "YAHOO_URLS",
        "https://store.shopping.yahoo.co.jp/characterland/4521329462233-b.html",
    ).split(",")
    if x.strip()
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("stock-alert")
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
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
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


def fetch_apple_products() -> Dict[str, Product]:
    response = requests.get(APPLE_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    products: Dict[str, Product] = {}

    for link in soup.select('a[href*="/shop/product/"]'):
        href = str(link.get("href", "")).strip()
        if not href:
            continue
        container = link
        for _ in range(5):
            if container.parent is None:
                break
            container = container.parent
            text = normalize_text(container.get_text(" ", strip=True))
            if "円" in text and "iPhone" in text:
                break
        name = normalize_text(link.get_text(" ", strip=True))
        text = normalize_text(container.get_text(" ", strip=True))
        if not name or "iPhone" not in name:
            heading = container.find(["h2", "h3", "h4"])
            if heading:
                name = normalize_text(heading.get_text(" ", strip=True))
        if not name or "iPhone" not in name or not matches_apple_keywords(name):
            continue
        price_match = re.search(r"[\d,]+円", text)
        price = price_match.group(0) if price_match else "Không rõ giá"
        url = urljoin("https://www.apple.com", href).split("?")[0]
        products[url] = Product(url, name, price, url, source="Apple")

    if not products:
        raise RuntimeError("Không đọc được sản phẩm Apple; cấu trúc trang có thể đã đổi.")
    return products


def rakuten_item_allowed(name: str) -> bool:
    lowered = normalize_text(name).lower()
    required = any(word in lowered for word in ("box", "ボックス", "30パック", "24パック", "未開封"))
    blocked = (
        "カード単品", "シングルカード", "オリパ", "福袋", "中古", "空箱", "箱のみ",
        "パック単品", "1パック", "バラ", "サプライ", "スリーブ", "デッキケース", "プレイマット",
    )
    return required and not any(word in lowered for word in blocked)


def fetch_rakuten_query(keyword: str) -> Dict[str, Product]:
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
        "maxPrice": RAKUTEN_MAX_PRICE,
        "elements": "itemName,itemPrice,itemUrl,itemCode,availability,shopName",
    }
    if RAKUTEN_AFFILIATE_ID:
        params["affiliateId"] = RAKUTEN_AFFILIATE_ID

    response = requests.get(RAKUTEN_ENDPOINT, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    products: Dict[str, Product] = {}
    for item in payload.get("items", []):
        name = normalize_text(str(item.get("itemName", "")))
        if not name or not rakuten_item_allowed(name):
            continue
        url = str(item.get("itemUrl", "")).strip()
        if not url:
            continue
        item_code = str(item.get("itemCode", "")).strip()
        key_raw = item_code or url.split("?")[0]
        key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
        price_value = item.get("itemPrice")
        price = f"¥{int(price_value):,}" if isinstance(price_value, (int, float)) else "Không rõ giá"
        shop = normalize_text(str(item.get("shopName", "")))
        products[key] = Product(key, name, price, url, shop=shop, source=f"Rakuten: {keyword}")
    logger.info("Rakuten [%s]: nhận %d sản phẩm hợp lệ.", keyword, len(products))
    return products


def fetch_all_rakuten_products() -> Dict[str, Product]:
    if not RAKUTEN_APP_ID or not RAKUTEN_ACCESS_KEY:
        raise RuntimeError("Thiếu RAKUTEN_APP_ID hoặc RAKUTEN_ACCESS_KEY")
    products: Dict[str, Product] = {}
    for index, keyword in enumerate(RAKUTEN_KEYWORDS):
        products.update(fetch_rakuten_query(keyword))
        if index < len(RAKUTEN_KEYWORDS) - 1:
            time.sleep(1)
    return products


def yahoo_page_in_stock(page_text: str) -> bool:
    text = normalize_text(page_text)
    positive = ("カートに入れる", "注文する", "購入手続きへ", "今すぐ購入", "予約する")
    negative = ("在庫切れ", "売り切れ", "販売終了", "現在在庫切れ", "この商品は現在販売しておりません")
    return any(word in text for word in positive) and not any(word in text for word in negative)


def fetch_yahoo_products() -> Dict[str, Product]:
    products: Dict[str, Product] = {}
    for url in YAHOO_URLS:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title = normalize_text((soup.title.get_text(" ", strip=True) if soup.title else url))
        in_stock = yahoo_page_in_stock(soup.get_text(" ", strip=True))
        logger.info("Yahoo: %s | %s", "CÓ HÀNG" if in_stock else "hết hàng", title[:100])
        if in_stock:
            price_match = re.search(r"[\d,]+円", normalize_text(soup.get_text(" ", strip=True)))
            price = price_match.group(0) if price_match else "Không rõ giá"
            key = hashlib.sha256(url.encode("utf-8")).hexdigest()
            products[key] = Product(key, title, price, url, source="Yahoo Shopping")
    return products


def product_message(product: Product) -> str:
    shop = f"\n🏪 {html.escape(product.shop)}" if product.shop else ""
    return (
        f"🚨 <b>{html.escape(product.source.upper())} CÓ HÀNG MỚI</b>\n\n"
        f"📦 {html.escape(product.name)}{shop}\n"
        f"💴 {html.escape(product.price)}\n\n"
        f'<a href="{html.escape(product.url, quote=True)}">MỞ TRANG MUA NGAY</a>'
    )


def notify_new(previous: Dict[str, Product], current: Dict[str, Product]) -> None:
    for key in sorted(current.keys() - previous.keys()):
        product = current[key]
        telegram_send(product_message(product))
        logger.info("Đã gửi cảnh báo: %s", product.name)
        time.sleep(1)


def run_source(
    name: str,
    fetcher: Callable[[], Dict[str, Product]],
    previous: Dict[str, Product] | None,
) -> Dict[str, Product]:
    current = fetcher()
    logger.info("%s: tổng cộng %d sản phẩm đang có hàng.", name, len(current))
    if previous is None:
        logger.info("%s: đã tạo mốc ban đầu, không gửi hàng loạt.", name)
    else:
        notify_new(previous, current)
    return current


def main() -> None:
    validate_config()
    rakuten_enabled = bool(RAKUTEN_APP_ID and RAKUTEN_ACCESS_KEY)
    yahoo_enabled = bool(YAHOO_URLS)

    if SEND_STARTUP_MESSAGE:
        telegram_send(
            "✅ <b>Stock Alert đã khởi động</b>\n\n"
            f"🍎 Apple: mỗi {CHECK_INTERVAL} giây\n"
            f"🎴 Rakuten: {'mỗi ' + str(RAKUTEN_INTERVAL) + ' giây' if rakuten_enabled else 'CHƯA BẬT'}\n"
            f"🛒 Yahoo: {'mỗi ' + str(YAHOO_INTERVAL) + ' giây' if yahoo_enabled else 'CHƯA BẬT'}\n\n"
            "Lần kiểm tra đầu chỉ tạo mốc, không gửi hàng loạt."
        )

    previous: Dict[str, Dict[str, Product] | None] = {"Apple": None, "Rakuten": None, "Yahoo": None}
    last_check = {"Apple": 0.0, "Rakuten": 0.0, "Yahoo": 0.0}
    intervals = {"Apple": CHECK_INTERVAL, "Rakuten": RAKUTEN_INTERVAL, "Yahoo": YAHOO_INTERVAL}
    fetchers: Dict[str, Callable[[], Dict[str, Product]]] = {"Apple": fetch_apple_products}
    if rakuten_enabled:
        fetchers["Rakuten"] = fetch_all_rakuten_products
    if yahoo_enabled:
        fetchers["Yahoo"] = fetch_yahoo_products

    errors = {name: 0 for name in fetchers}
    while not stop_requested:
        for name, fetcher in fetchers.items():
            now = time.monotonic()
            if now - last_check[name] < intervals[name]:
                continue
            last_check[name] = now
       try:
                previous[name] = run_source(name, fetcher, previous[name])
                errors[name] = 0
            except Exception as exc:
                errors[name] += 1
                logger.exception(
                    "%s lỗi lần %d: %s",
                    name,
                    errors[name],
                    exc,
                )

                if name == "Rakuten" and "403" in str(exc):
                    continue

                if errors[name] in {3, 10}:
                    try:
                        telegram_send(
                            f"⚠️ <b>{html.escape(name)} đang gặp lỗi</b>\n\n"
                            f"{html.escape(str(exc))}\n\n"
                            "Bot sẽ tự thử lại."
                        )
                    except Exception:
                        logger.exception(
                            "Không gửi được lỗi %s lên Telegram.",
                            name,
                        )

    logger.info("Bot đã dừng an toàn.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("Bot không thể khởi động: %s", exc)
        sys.exit(1)
