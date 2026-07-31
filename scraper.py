import os
import logging
import re
import random
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup
from botasaurus.browser import browser, Driver
from database import (
    record_price_snapshot,
    generate_baseline_history_if_needed,
    get_price_history_analytics,
)

# Configure logger
logger = logging.getLogger("multi_scraper")
logger.setLevel(logging.INFO)

AMAZON_DOMAINS = {
    "IN": "https://www.amazon.in",
    "US": "https://www.amazon.com",
    "UK": "https://www.amazon.co.uk",
    "GB": "https://www.amazon.co.uk",
    "CA": "https://www.amazon.ca",
    "DE": "https://www.amazon.de",
    "FR": "https://www.amazon.fr",
    "JP": "https://www.amazon.co.jp",
    "AU": "https://www.amazon.com.au",
}

COUNTRY_CURRENCY_MAP = {
    "IN": "INR",
    "US": "USD",
    "UK": "GBP",
    "GB": "GBP",
    "CA": "CAD",
    "DE": "EUR",
    "FR": "EUR",
    "JP": "JPY",
    "AU": "AUD",
}


def get_country_currency(country_code: str = "IN") -> str:
    code = (country_code or "IN").upper().strip()
    return COUNTRY_CURRENCY_MAP.get(code, "INR")


def extract_numeric_price(price_str: str, default_currency: str = "INR") -> tuple[float | None, str | None]:
    if not price_str or price_str == "N/A":
        return None, default_currency

    currency = default_currency
    if "₹" in price_str or "INR" in price_str:
        currency = "INR"
    elif "£" in price_str:
        currency = "GBP"
    elif "€" in price_str:
        currency = "EUR"
    elif "C$" in price_str or "CDN" in price_str:
        currency = "CAD"
    elif "¥" in price_str:
        currency = "JPY"
    elif "$" in price_str:
        currency = "USD"

    clean_str = re.sub(r"[^\d.]", "", price_str.replace(",", ""))
    try:
        val = float(clean_str)
        return val, currency
    except (ValueError, TypeError):
        return None, currency


def clean_currency_symbol(price_str: str) -> str:
    """
    Strips currency symbols (₹, $, £, €, INR, USD, etc.) from price string
    so frontend can render its own currency formatting.
    e.g. '₹1,499' -> '1,499', '₹5,999' -> '5,999', 'Rs.5999.0' -> '5999.0'
    """
    if not price_str or price_str == "N/A":
        return "N/A"
    cleaned = re.sub(r"[₹$£€¥]|INR|USD|GBP|EUR|CAD|Rs\.?", "", price_str, flags=re.IGNORECASE).strip()
    return cleaned if cleaned else "N/A"



def get_amazon_base_url(country_code: str = "IN") -> tuple[str, str]:
    code = (country_code or "IN").upper().strip()
    base_domain = AMAZON_DOMAINS.get(code, "https://www.amazon.in")
    return f"{base_domain}/s", base_domain


def build_amazon_url(data: dict, page: int = 1) -> tuple[str, str]:
    country_code = data.get("country_code", "IN")
    search_url, base_domain = get_amazon_base_url(country_code)

    params = {}
    query = data.get("query") or data.get("q")
    if query:
        params["k"] = str(query).strip()

    if page > 1:
        params["page"] = str(page)
        params["ref"] = f"sr_pg_{page}"

    sort = data.get("sort")
    if sort:
        params["sort"] = str(sort).strip()

    tags = data.get("tags")
    rh_parts = []
    if tags:
        rh_parts.append(str(tags).strip())

    if data.get("is_deals_only"):
        rh_parts.append("p_n_specials_match:21612407031")

    min_discount = data.get("min_discount")
    if min_discount is not None and str(min_discount).isdigit():
        disc_val = int(min_discount)
        if disc_val >= 50:
            rh_parts.append("p_85:50-")
        elif disc_val >= 30:
            rh_parts.append("p_85:30-")
        elif disc_val >= 20:
            rh_parts.append("p_85:20-")
        elif disc_val >= 10:
            rh_parts.append("p_85:10-")

    low_price = data.get("low_price")
    high_price = data.get("high_price")

    if low_price is not None and str(low_price).strip() != "":
        params["low-price"] = str(low_price).strip()

    if high_price is not None and str(high_price).strip() != "":
        params["high-price"] = str(high_price).strip()

    if low_price is not None or high_price is not None:
        min_cents = ""
        max_cents = ""
        if low_price is not None and str(low_price).strip().isdigit():
            min_cents = str(int(low_price) * 100)
        if high_price is not None and str(high_price).strip().isdigit():
            max_cents = str(int(high_price) * 100)

        if min_cents or max_cents:
            price_rh = f"p_36:{min_cents}-{max_cents}"
            rh_parts.append(price_rh)

    if rh_parts:
        params["rh"] = ",".join(rh_parts)

    encoded_params = urlencode(params)
    full_url = f"{search_url}?{encoded_params}" if encoded_params else search_url
    return full_url, base_domain


def sanitize_title(title: str) -> str:
    """
    Cleans extracted title text and removes duplicated concatenated strings
    resulting from screen-reader accessible HTML spans.
    """
    if not title or title == "N/A":
        return "N/A"

    t = re.sub(r"\s+", " ", title).strip()

    for L in range(10, min(60, len(t) // 2 + 1)):
        prefix = t[:L]
        second_pos = t.find(prefix, L)
        if second_pos != -1:
            t = t[:second_pos].strip()
            break

    return t if len(t) > 0 else "N/A"


def parse_product_grid(html_content: str, base_domain: str = "https://www.amazon.in", country_code: str = "IN") -> list[dict]:
    soup = BeautifulSoup(html_content, "html.parser")
    products = []
    seen_asins = set()
    default_curr = get_country_currency(country_code)

    page_title = soup.title.string.strip() if soup.title and soup.title.string else ""
    if "Sorry! Something went wrong!" in page_title or "Robot Check" in page_title:
        logger.warning(f"Amazon blocked request with page title: '{page_title}'")
        return []

    items = soup.find_all("div", attrs={"data-asin": lambda a: a and len(a.strip()) == 10})

    for item in items:
        asin = item.get("data-asin", "").strip()
        if not asin or asin in seen_asins:
            continue

        title = None
        h2 = item.find("h2")
        if h2:
            spans = [s for s in h2.find_all("span") if "a-offscreen" not in s.get("class", [])]
            if spans:
                title = spans[0].get_text(strip=True)
            else:
                title = h2.get_text(strip=True)

        if not title:
            title_elem = (
                item.find("span", class_="a-text-normal") or
                item.find("span", class_=lambda c: c and ("a-size-medium" in c or "a-size-base-plus" in c or "a-size-base" in c))
            )
            if title_elem:
                title = title_elem.get_text(strip=True)

        title = sanitize_title(title)

        if title == "N/A" or len(title) < 3:
            continue

        seen_asins.add(asin)
        clean_product_url = f"{base_domain}/dp/{asin}"

        price = None
        price_elem = item.find("span", class_="a-price")
        if price_elem:
            offscreen = price_elem.find("span", class_="a-offscreen")
            if offscreen:
                price = offscreen.get_text(strip=True)
            else:
                whole = price_elem.find("span", class_="a-price-whole")
                fraction = price_elem.find("span", class_="a-price-fraction")
                if whole:
                    price_str = whole.get_text(strip=True)
                    if fraction:
                        price_str += "." + fraction.get_text(strip=True)
                    price = price_str

        original_price = None
        strike_elem = item.find("span", class_=lambda c: c and ("a-text-strike" in c or "a-text-price" in c or "a-price-strike" in c))
        if strike_elem:
            off_span = strike_elem.find("span", class_="a-offscreen")
            if off_span:
                original_price = off_span.get_text(strip=True)
            else:
                original_price = strike_elem.get_text(strip=True)

        if not original_price:
            m_mrp = re.search(r"M\.?R\.?P\.?:?\s*₹?\s*([\d,]+(?:\.\d{2})?)", item.get_text())
            if m_mrp:
                original_price = f"₹{m_mrp.group(1)}"

        discount = None
        disc_match = re.search(r"\b(-\d{1,2}%|\d{1,2}%\s*off)\b", item.get_text(), re.IGNORECASE)
        if disc_match:
            discount = disc_match.group(1).lstrip("-").strip()
            if "off" not in discount.lower():
                discount = f"{discount} off"

        price_num, currency = extract_numeric_price(price, default_currency=default_curr)
        orig_price_num, _ = extract_numeric_price(original_price, default_currency=default_curr)

        if price_num and orig_price_num and orig_price_num > price_num:
            disc_pct = int(round((1.0 - (price_num / orig_price_num)) * 100))
            if disc_pct > 0:
                discount = f"{disc_pct}% off"
        elif not discount:
            discount = "0%"

        if asin and price_num:
            record_price_snapshot(asin, country_code, title or "", price_num, orig_price_num or price_num, currency or default_curr)

        rating = None
        rating_elem = item.find("i", class_=lambda c: c and "a-icon-star" in c) or item.find("span", class_="a-icon-alt")
        if rating_elem:
            rating = rating_elem.get_text(strip=True)
        if not rating:
            star_elem = item.find("span", attrs={"aria-label": lambda a: a and "out of 5 stars" in a})
            if star_elem:
                rating = star_elem.get("aria-label", "").strip()

        review_count = "0"
        review_elem = item.find("span", class_="s-underline-text") or item.find("span", class_=lambda c: c and "a-size-base" in c and "s-underline-text" in c)
        if review_elem:
            review_count = review_elem.get_text(strip=True).strip("()")
        else:
            aria_review = item.find("span", attrs={"aria-label": lambda a: a and ("ratings" in a or "rating" in a or "reviews" in a)})
            if aria_review:
                review_count = aria_review.get_text(strip=True).strip("()")

        is_sponsored = bool(item.find("span", class_=lambda c: c and "s-sponsored-label" in c) or
                            item.find("span", attrs={"data-component-type": "s-sponsored-label-info-icon"}) or
                            "Sponsored" in item.get_text())

        is_prime = bool(item.find("i", class_=lambda c: c and "a-icon-prime" in c) or
                        item.find("span", attrs={"aria-label": "Amazon Prime"}) or
                        item.find("i", attrs={"aria-label": "Amazon Prime"}))

        badge = None
        badge_elem = item.find("span", class_=lambda c: c and ("a-badge-text" in c or "a-badge-label" in c))
        if badge_elem:
            badge = badge_elem.get_text(strip=True)

        is_deal = bool((discount and discount != "0%") or (badge and ("deal" in badge.lower() or "choice" in badge.lower() or "bestseller" in badge.lower())))

        image_url = None
        img_elem = item.find("img", class_="s-image")
        if img_elem:
            image_url = img_elem.get("src") or img_elem.get("data-src")

        price_clean = clean_currency_symbol(price) if price else "N/A"
        orig_price_clean = clean_currency_symbol(original_price or price) if (original_price or price) else "N/A"

        products.append({
            "platform": "amazon",
            "asin": asin,
            "title": title,
            "price": price_clean,
            "price_numeric": price_num,
            "original_price": orig_price_clean,
            "original_price_numeric": orig_price_num or price_num,
            "currency": currency or default_curr,
            "discount": discount or "0%",
            "rating": rating or "N/A",
            "review_count": review_count or "0",
            "is_sponsored": is_sponsored,
            "is_prime": is_prime,
            "is_deal": is_deal,
            "badge": badge or "",
            "image_url": image_url or "",
            "product_url": clean_product_url
        })

    return products


def parse_bestsellers_grid(html_content: str, base_domain: str = "https://www.amazon.in", country_code: str = "IN") -> list[dict]:
    soup = BeautifulSoup(html_content, "html.parser")
    products = []
    seen_asins = set()
    default_curr = get_country_currency(country_code)

    cards = soup.find_all("div", id=lambda i: i and i.startswith("post-"))
    if not cards:
        cards = soup.find_all("div", class_=lambda c: c and "zg-grid-general-faceout" in c)
    if not cards:
        cards = [d for d in soup.find_all("div", attrs={"data-asin": True}) if d.get("data-asin")]

    for card in cards:
        title_elem = (
            card.find("div", class_=lambda cl: cl and "p13n-sc-css-line-clamp" in cl) or
            card.find("span", class_=lambda cl: cl and "zg-text-js-truncate" in cl) or
            card.find("div", class_=lambda cl: cl and "p13n-sc-truncate" in cl) or
            card.find("a", class_=lambda cl: cl and "a-link-normal" in cl)
        )
        title = title_elem.get_text(strip=True) if title_elem else "N/A"
        title = sanitize_title(title)

        if title == "N/A" or len(title) < 3:
            continue

        asin = card.get("data-asin", "").strip()
        link = card.find("a", class_=lambda cl: cl and "a-link-normal" in cl)
        if link and link.get("href") and not asin:
            match = re.search(r"/dp/([A-Z0-9]{10})", link.get("href"))
            if match:
                asin = match.group(1)

        if not asin or asin in seen_asins:
            continue

        seen_asins.add(asin)
        clean_product_url = f"{base_domain}/dp/{asin}"

        price = "N/A"
        price_elem = card.find("span", class_=lambda cl: cl and "p13n-sc-price" in cl) or card.find("span", class_="a-price")
        if price_elem:
            off = price_elem.find("span", class_="a-offscreen")
            price = off.get_text(strip=True) if off else price_elem.get_text(strip=True)

        price_num, currency = extract_numeric_price(price, default_currency=default_curr)

        rating = "N/A"
        rating_elem = card.find("i", class_=lambda c: c and "a-icon-star" in c) or card.find("span", class_="a-icon-alt")
        if rating_elem:
            rating = rating_elem.get_text(strip=True)

        review_count = "0"
        rev_elem = card.find("span", class_=lambda c: c and ("a-size-small" in c or "s-underline-text" in c))
        if rev_elem and rev_elem.get_text(strip=True).replace(",", "").isdigit():
            review_count = rev_elem.get_text(strip=True)

        image_url = ""
        img_elem = card.find("img")
        if img_elem:
            image_url = img_elem.get("src") or img_elem.get("data-src") or ""

        if asin and price_num:
            record_price_snapshot(asin, country_code, title or "", price_num, price_num, currency or default_curr)

        products.append({
            "platform": "amazon",
            "asin": asin,
            "title": title,
            "price": price,
            "price_numeric": price_num,
            "original_price": price,
            "original_price_numeric": price_num,
            "currency": currency or default_curr,
            "discount": "0%",
            "rating": rating,
            "review_count": review_count,
            "is_sponsored": False,
            "is_prime": bool(card.find("i", class_=lambda c: c and "a-icon-prime" in c)),
            "is_deal": False,
            "badge": "Bestseller",
            "image_url": image_url,
            "product_url": clean_product_url
        })

    return products


def parse_flipkart_grid(html_content: str) -> list[dict]:
    soup = BeautifulSoup(html_content, "html.parser")
    products = []
    seen_ids = set()

    cards = soup.find_all("div", attrs={"data-id": True})
    if not cards:
        cards = soup.find_all("div", class_=lambda c: c and ("_1AtVbE" in c or "_75W9fW" in c or "cPHxW0" in c or "_1sd2w" in c or "slpT2d" in c or "_4ddWXP" in c))

    for card in cards:
        title = "N/A"
        anchors = card.find_all("a")
        for a in anchors:
            t_attr = a.get("title", "").strip()
            if t_attr and len(t_attr) > 3:
                title = t_attr
                break

        if title == "N/A":
            img = card.find("img")
            if img and img.get("alt") and len(img.get("alt").strip()) > 3:
                title = img.get("alt").strip()

        if title == "N/A":
            title_elem = (
                card.find("div", class_=lambda c: c and ("_4rR01T" in c or "KzppSp" in c or "wN21h9" in c)) or
                card.find("a", class_=lambda c: c and ("atJtCj" in c or "s1Q98w" in c or "IRyWSu" in c or "W5uif4" in c))
            )
            if title_elem:
                title = title_elem.get_text(strip=True)

        if title == "N/A" or len(title.strip()) < 3:
            continue

        link_elem = card.find("a", href=True)
        raw_href = link_elem["href"] if link_elem else ""
        pid = card.get("data-id", "").strip()

        if raw_href:
            parsed = urlparse(raw_href)
            qs = parse_qs(parsed.query)
            if qs.get("pid"):
                pid = qs["pid"][0]

        if not pid:
            match = re.search(r"pid=([A-Za-z0-9]+)", raw_href) or re.search(r"/p/([A-Za-z0-9]+)", raw_href)
            if match:
                pid = match.group(1)

        if not pid:
            pid = re.sub(r"[^\w]", "", title[:20])

        if pid in seen_ids:
            continue

        seen_ids.add(pid)

        clean_path = raw_href.split("?")[0] if raw_href else ""
        clean_product_url = f"https://www.flipkart.com{clean_path}?pid={pid}" if clean_path else f"https://www.flipkart.com/p/p?pid={pid}"

        price = "N/A"
        price_elem = card.find("div", class_=lambda c: c and ("hZ3P6w" in c or "DeU9vF" in c or "_30jeq3" in c or "Nx9qKw" in c or "_25bRAu" in c or "D29T0n" in c))
        if price_elem:
            price = price_elem.get_text(strip=True)
        else:
            text = card.get_text()
            match = re.search(r"₹\s*([\d,]+)", text)
            if match:
                price = f"₹{match.group(1)}"

        original_price = price
        orig_elem = card.find("div", class_=lambda c: c and ("kRYCnD" in c or "gxR4EY" in c or "_3I9_wc" in c or "yVaB0w" in c))
        if orig_elem:
            original_price = orig_elem.get_text(strip=True)

        discount = "0%"
        disc_match = re.search(r"\b(\d{1,2}%\s*off)", card.get_text(), re.IGNORECASE)
        if disc_match:
            discount = disc_match.group(1)

        price_num, currency = extract_numeric_price(price, default_currency="INR")
        orig_price_num, _ = extract_numeric_price(original_price, default_currency="INR")

        if not disc_match and price_num and orig_price_num and orig_price_num > price_num:
            disc_pct = int(round((1.0 - (price_num / orig_price_num)) * 100))
            if disc_pct > 0:
                discount = f"{disc_pct}% off"

        card_text = card.get_text(" ", strip=True)

        is_sponsored = bool(re.search(r"\b(Ad|Sponsored|Promoted)\b", card_text) or
                            card.find("div", class_=lambda c: c and ("_2I90oD" in c or "cPHxW0" in c)))

        badge = ""
        if "Special price" in card_text or "Special Price" in card_text:
            badge = "Special Price"
        elif "Hot Deal" in card_text:
            badge = "Hot Deal"
        elif "Daily Saver" in card_text:
            badge = "Daily Saver"
        elif "Bestseller" in card_text:
            badge = "Bestseller"
        elif "Trending" in card_text:
            badge = "Trending"
        elif discount and discount != "0%":
            badge = f"Special Deal ({discount})"
        else:
            badge = "Featured"

        is_deal = bool((discount and discount != "0%") or badge in ["Special Price", "Hot Deal", "Daily Saver", "Bestseller"])

        rating = "N/A"
        rating_elem = (
            card.find("span", class_=lambda c: c and ("CjyrHS" in c or "_1lR2r2" in c)) or
            card.find("div", class_=lambda c: c and ("MKiFS6" in c or "XQBx0U" in c or "_3LWZlK" in c or "_5O7F6f" in c))
        )
        if rating_elem:
            r_txt = rating_elem.get_text(strip=True)
            m = re.search(r"(\d\.\d)", r_txt)
            if m:
                rating = f"{m.group(1)} out of 5 stars"

        if rating == "N/A":
            for child in card.find_all(["span", "div"]):
                ctxt = child.get_text(strip=True)
                if len(ctxt) < 15:
                    m = re.search(r"^(\d\.\d)$", ctxt)
                    if m:
                        rating = f"{m.group(1)} out of 5 stars"
                        break

        if pid and price_num:
            record_price_snapshot(pid, "IN", title or "", price_num, orig_price_num or price_num, "INR")

        price_clean = clean_currency_symbol(price) if price else "N/A"
        orig_price_clean = clean_currency_symbol(original_price or price) if (original_price or price) else "N/A"

        products.append({
            "platform": "flipkart",
            "product_id": pid,
            "asin": pid,
            "title": title,
            "price": price_clean,
            "price_numeric": price_num,
            "original_price": orig_price_clean,
            "original_price_numeric": orig_price_num or price_num,
            "currency": "INR",
            "discount": discount,
            "rating": rating,
            "is_sponsored": is_sponsored,
            "is_deal": is_deal,
            "badge": badge,
            "image_url": img.get("src") if (img := card.find("img")) else "",
            "product_url": clean_product_url
        })

    return products


def parse_flipkart_product_details(html_content: str, pid: str) -> dict:
    soup = BeautifulSoup(html_content, "html.parser")

    t_elem = soup.find("span", class_=lambda c: c and ("VU-Bz7" in c or "B_NuT2" in c or "m5221" in c)) or soup.find("h1")
    title = t_elem.get_text(strip=True) if t_elem else "N/A"
    if title == "N/A" and soup.title:
        title = soup.title.get_text(strip=True).split(" Price in India")[0]

    title = sanitize_title(title)

    price = "N/A"
    original_price = "N/A"
    rating = "N/A"

    # Strategy 0 (JSON-LD Schema - 100% authoritative for Flipkart product pages):
    for script in soup.find_all("script", type="application/ld+json"):
        if script.string and '"offers"' in script.string:
            try:
                import json
                ld_data = json.loads(script.string)
                if isinstance(ld_data, list):
                    ld_data = ld_data[0]
                if isinstance(ld_data, dict) and "offers" in ld_data:
                    offers = ld_data["offers"]
                    if isinstance(offers, dict) and "price" in offers:
                        p_val = offers["price"]
                        if p_val:
                            p_int = int(float(p_val))
                            price = f"{p_int:,}" if p_int > 0 else str(p_val)

                            desc = ld_data.get("description", "")
                            m_orig = re.search(r"for\s+Rs\.?\s*([\d.]+)", desc, re.IGNORECASE)
                            if m_orig:
                                o_int = int(float(m_orig.group(1)))
                                original_price = f"{o_int:,}" if o_int > 0 else str(m_orig.group(1))

                    if "aggregateRating" in ld_data and isinstance(ld_data["aggregateRating"], dict):
                        r_val = ld_data["aggregateRating"].get("ratingValue")
                        if r_val:
                            rating = str(r_val)
                    break
            except Exception:
                pass

    if price == "N/A":
        p_elem = (
            soup.find("div", class_=lambda c: c and "v1zwn21l" in c and "v1zwn20" in c) or
            soup.find("div", class_=lambda c: c and ("Nx9qKw" in c or "_30jeq3" in c or "C22vL7" in c or "css-g5y9jx" in c)) or
            soup.find("div", class_=lambda c: c and "hZ3P6w" in c)
        )
        if p_elem:
            price = p_elem.get_text(strip=True)
        else:
            for elem in soup.find_all(["div", "span"]):
                txt = elem.get_text(strip=True)
                if re.match(r"^\s*₹?\s*[\d,]{2,7}\s*$", txt):
                    price = txt
                    break

    if original_price == "N/A":
        orig_elem = soup.find("div", class_=lambda c: c and ("v1zwn21m" in c or "yVaB0w" in c or "_3I9_wc" in c or "kRYCnD" in c))
        if orig_elem:
            original_price = orig_elem.get_text(strip=True)
        else:
            original_price = price

    if rating == "N/A":
        rating_elem = soup.find("div", class_=lambda c: c and ("XQBx0U" in c or "_3LWZlK" in c or "_5O7F6f" in c))
        rating = rating_elem.get_text(strip=True) if rating_elem else "N/A"

    price_clean = clean_currency_symbol(price)
    orig_price_clean = clean_currency_symbol(original_price)
    price_num, currency = extract_numeric_price(price_clean, default_currency="INR")
    orig_price_num, _ = extract_numeric_price(orig_price_clean, default_currency="INR")

    avail_elem = soup.find("div", class_=lambda c: c and ("_16FRp0" in c or "out-of-stock" in (c.lower() if isinstance(c, str) else "") if c else False))
    availability = avail_elem.get_text(strip=True) if avail_elem else "In Stock"

    seller_elem = soup.find("div", class_=lambda c: c and ("_1RLvfY" in c or "SellerDetails" in (c if isinstance(c, str) else "") if c else False))
    seller = seller_elem.get_text(strip=True) if seller_elem else "Flipkart Assured Seller"

    images = []
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and "rukminim" in src and src not in images:
            images.append(src)

    return {
        "platform": "flipkart",
        "product_id": pid,
        "asin": pid,
        "title": title,
        "price": price_clean,
        "price_numeric": price_num,
        "original_price": orig_price_clean,
        "original_price_numeric": orig_price_num or price_num,
        "currency": "INR",
        "availability": availability,
        "seller": seller,
        "rating": rating,
        "images": images[:5],
        "product_url": f"https://www.flipkart.com/p/p?pid={pid}"
    }


def parse_product_details(html_content: str, asin: str, base_domain: str = "https://www.amazon.in", country_code: str = "IN") -> dict:
    soup = BeautifulSoup(html_content, "html.parser")
    default_curr = get_country_currency(country_code)

    title_elem = soup.find(id="productTitle") or soup.find("span", id="productTitle")
    raw_title = title_elem.get_text(strip=True) if title_elem else "N/A"
    title = sanitize_title(raw_title)

    price = None
    price_elem = soup.find("span", class_="a-price") or soup.find(id="priceblock_ourprice") or soup.find(id="priceblock_dealprice")
    if price_elem:
        offscreen = price_elem.find("span", class_="a-offscreen")
        price = offscreen.get_text(strip=True) if offscreen else price_elem.get_text(strip=True)

    original_price = None
    strike_elem = soup.find("span", class_=lambda c: c and ("a-text-strike" in c or "a-text-price" in c or "a-price-strike" in c or "basisPrice" in c or "apex-basisprice-value" in c))
    if strike_elem:
        off_span = strike_elem.find("span", class_="a-offscreen")
        if off_span:
            original_price = off_span.get_text(strip=True)
        else:
            original_price = strike_elem.get_text(strip=True)

    price_num, currency = extract_numeric_price(price, default_currency=default_curr)
    orig_price_num, _ = extract_numeric_price(original_price, default_currency=default_curr)

    if asin and price_num:
        record_price_snapshot(asin, country_code, title or "", price_num, orig_price_num or price_num, currency or default_curr)

    avail_elem = soup.find(id="availability")
    availability = avail_elem.get_text(strip=True) if avail_elem else "In Stock"

    merchant_elem = soup.find(id="merchant-info")
    seller = merchant_elem.get_text(strip=True) if merchant_elem else "Ships from Amazon"

    brand_elem = soup.find(id="bylineInfo") or soup.find("a", class_="a-link-normal")
    brand = brand_elem.get_text(strip=True) if brand_elem else "N/A"

    rating_elem = soup.find(id="acrPopover") or soup.find("i", class_=lambda c: c and "a-icon-star" in c)
    rating = rating_elem.get_text(strip=True) if rating_elem else "N/A"

    reviews_elem = soup.find(id="acrCustomerReviewText")
    review_count = reviews_elem.get_text(strip=True) if reviews_elem else "0"

    bullets = []
    bullet_div = soup.find(id="feature-bullets")
    if bullet_div:
        for li in bullet_div.find_all("li"):
            text = li.get_text(strip=True)
            if text and not text.startswith("P.when"):
                bullets.append(text)

    images = []
    landing_img = soup.find("img", id="landingImage") or soup.find("img", id="imgBlkFront")
    if landing_img and landing_img.get("src"):
        images.append(landing_img.get("src"))

    alt_img_div = soup.find(id="altImages")
    if alt_img_div:
        for img in alt_img_div.find_all("img"):
            src = img.get("src")
            if src and "media-amazon.com" in src and src not in images:
                high_res = re.sub(r'\._SS\d+_\.', '.', src)
                images.append(high_res)

    price_clean = clean_currency_symbol(price) if price else "N/A"
    orig_price_clean = clean_currency_symbol(original_price or price) if (original_price or price) else "N/A"

    return {
        "asin": asin,
        "title": title,
        "price": price_clean,
        "price_numeric": price_num,
        "original_price": orig_price_clean,
        "original_price_numeric": orig_price_num or price_num,
        "currency": currency or default_curr,
        "brand": brand,
        "availability": availability,
        "seller": seller,
        "rating": rating,
        "review_count": review_count,
        "features": bullets,
        "images": images,
        "product_url": f"{base_domain}/dp/{asin}"
    }


def parse_product_reviews(html_content: str, asin: str) -> list[dict]:
    soup = BeautifulSoup(html_content, "html.parser")
    reviews = []

    review_cards = soup.find_all("div", attrs={"data-hook": "review"})
    if not review_cards:
        review_cards = soup.find_all("div", class_=lambda c: c and "review" in c and "a-section" in c)

    for card in review_cards:
        review_id = card.get("id", "")
        author_elem = card.find("span", class_="a-profile-name")
        rating_elem = card.find("i", attrs={"data-hook": lambda h: h and ("review-star-rating" in h or "cmps-review-star-rating" in h)}) or card.find("i", class_=lambda c: c and "a-icon-star" in c)
        title_elem = card.find("a", attrs={"data-hook": "review-title"}) or card.find("span", attrs={"data-hook": "review-title"}) or card.find("a", class_=lambda c: c and "review-title" in c)
        date_elem = card.find("span", attrs={"data-hook": "review-date"}) or card.find("span", class_=lambda c: c and "review-date" in c)
        verified_elem = card.find("span", attrs={"data-hook": "avp-badge"}) or card.find("i", attrs={"data-hook": "avp-badge"})
        body_elem = card.find("span", attrs={"data-hook": "review-body"}) or card.find("div", class_=lambda c: c and "review-body" in c)

        content = ""
        if body_elem:
            body_span = body_elem.find("span")
            content = body_span.get_text(strip=True) if body_span else body_elem.get_text(strip=True)

        title_text = "N/A"
        if title_elem:
            t_span = title_elem.find("span")
            title_text = t_span.get_text(strip=True) if t_span else title_elem.get_text(strip=True)

        reviews.append({
            "id": review_id,
            "author": author_elem.get_text(strip=True) if author_elem else "Anonymous",
            "rating": rating_elem.get_text(strip=True) if rating_elem else "N/A",
            "title": title_text,
            "date": date_elem.get_text(strip=True) if date_elem else "N/A",
            "verified_purchase": bool(verified_elem),
            "content": content
        })

    return reviews


def _scrape_products_internal(driver: Driver, data: dict) -> list[dict]:
    country_code = data.get("country_code", "IN")
    start_page = int(data.get("page") or data.get("start_page") or 1)
    raw_max = data.get("max_pages") or data.get("max_page") or start_page
    max_pages = min(max(int(raw_max), start_page), 50)

    all_products = []
    seen_asins = set()

    for page in range(start_page, max_pages + 1):
        target_url, base_domain = build_amazon_url(data, page=page)
        logger.info(f"Navigating to Page {page}/{max_pages}: {target_url}")
        driver.get(target_url)

        delay = random.uniform(2.0, 3.5)
        driver.sleep(delay)

        html_content = driver.page_html
        products = parse_product_grid(html_content, base_domain=base_domain, country_code=country_code)

        new_products = []
        for p in products:
            asin = p.get("asin")
            if asin and asin not in seen_asins:
                seen_asins.add(asin)
                new_products.append(p)

        logger.info(f"Page {page}: Extracted {len(new_products)} NEW unique products.")
        all_products.extend(new_products)

        if not new_products:
            logger.info(f"No new unique products found on Page {page}. Ending pagination loop.")
            break

    min_discount = data.get("min_discount")
    if min_discount is not None and str(min_discount).isdigit():
        target_disc = float(min_discount)
        filtered = []
        for p in all_products:
            disc_str = p.get("discount", "")
            match = re.search(r"(\d+)", disc_str)
            if match and float(match.group(1)) >= target_disc:
                filtered.append(p)
        return filtered

    return all_products


def _track_amazon_price_internal(driver: Driver, data: dict) -> dict:
    asin = data.get("asin", "").strip()
    country_code = data.get("country_code", "IN")
    target_price = data.get("target_price")
    _, base_domain = get_amazon_base_url(country_code)

    target_url = f"{base_domain}/dp/{asin}"
    logger.info(f"Price Tracker checking ASIN {asin}: {target_url}")

    driver.get(target_url)
    driver.sleep(random.uniform(1.5, 2.5))

    details = parse_product_details(driver.page_html, asin=asin, base_domain=base_domain, country_code=country_code)

    current_num = details.get("price_numeric")
    orig_num = details.get("original_price_numeric")
    default_curr = get_country_currency(country_code)

    if current_num:
        generate_baseline_history_if_needed(
            asin=asin,
            country_code=country_code,
            current_price=current_num,
            original_price=orig_num or current_num,
            currency=details.get("currency", default_curr),
            title=details.get("title", "")
        )

    # Fetch price history timeline
    days = int(data.get("days", 90))
    history_report = get_price_history_analytics(asin, country_code=country_code, days=days)

    savings_amount = 0.0
    savings_pct = 0.0
    if current_num and orig_num and orig_num > current_num:
        savings_amount = round(orig_num - current_num, 2)
        savings_pct = round((savings_amount / orig_num) * 100, 2)

    target_met = False
    status = "REGULAR_PRICE"

    if current_num:
        if orig_num and orig_num > current_num:
            status = "DEAL_ACTIVE"

        if target_price is not None:
            try:
                target_val = float(target_price)
                if current_num <= target_val:
                    target_met = True
                    status = "TARGET_PRICE_MET"
                else:
                    status = "ABOVE_TARGET"
            except (ValueError, TypeError):
                pass

    return {
        "platform": "amazon",
        "asin": asin,
        "title": details.get("title"),
        "country_code": country_code,
        "currency": details.get("currency", default_curr),
        "current_price": clean_currency_symbol(details.get("price")),
        "current_price_numeric": current_num,
        "original_price": clean_currency_symbol(details.get("original_price")),
        "original_price_numeric": orig_num,
        "savings_amount": savings_amount,
        "savings_percentage": savings_pct,
        "is_deal": bool(savings_amount > 0),
        "target_price": target_price,
        "target_price_met": target_met,
        "price_status": status,
        "availability": details.get("availability"),
        "seller": details.get("seller"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "product_url": details.get("product_url"),
        "lowest_price_ever": history_report.get("lowest_price_ever"),
        "highest_price_ever": history_report.get("highest_price_ever"),
        "average_price": history_report.get("average_price"),
        "price_trend": history_report.get("price_trend"),
        "price_history": history_report.get("history", [])
    }


def _track_flipkart_price_internal(driver: Driver, data: dict) -> dict:
    pid = (data.get("asin") or data.get("product_id") or "").strip()
    target_price = data.get("target_price")

    target_url = data.get("url")
    if not target_url:
        if pid.startswith("http://") or pid.startswith("https://"):
            target_url = pid
        else:
            # Use Flipkart's direct PID URL which shows the product page
            target_url = f"https://www.flipkart.com/product/p/itm?pid={pid}"

    logger.info(f"Flipkart Price Tracker checking PID {pid}: {target_url}")
    driver.get(target_url)
    driver.sleep(random.uniform(2.0, 3.0))

    soup = BeautifulSoup(driver.page_html, "html.parser")

    # If the PID URL loaded, find the real product link and navigate to it
    if "/product/p/itm?" in driver.current_url and pid:
        real_link = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if pid in href and "/p/itm" in href and not href.startswith("/account"):
                real_link = href
                break
        if real_link:
            full_url = f"https://www.flipkart.com{real_link}" if real_link.startswith("/") else real_link
            logger.info(f"Flipkart: Following real product link: {full_url[:150]}")
            driver.get(full_url)
            driver.sleep(random.uniform(1.5, 2.5))
            soup = BeautifulSoup(driver.page_html, "html.parser")

    # Build the canonical product URL
    product_url = driver.current_url
    if pid and "flipkart.com" not in product_url:
        product_url = f"https://www.flipkart.com/product/p/itm?pid={pid}"

    # --- Extract product details from the detail page ---

    # Title: Use H1 or fallback to page title
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else "N/A"
    if title == "N/A" and soup.title:
        title = soup.title.get_text(strip=True).split(" Price in India")[0].split(" - Buy")[0]
    title = sanitize_title(title)

    # Price & Original Price & Rating
    price = "N/A"
    original_price = "N/A"
    rating = "N/A"

    # Strategy 0 (JSON-LD Schema - 100% authoritative for Flipkart product pages):
    for script in soup.find_all("script", type="application/ld+json"):
        if script.string and '"offers"' in script.string:
            try:
                import json
                ld_data = json.loads(script.string)
                if isinstance(ld_data, list):
                    ld_data = ld_data[0]
                if isinstance(ld_data, dict) and "offers" in ld_data:
                    offers = ld_data["offers"]
                    if isinstance(offers, dict) and "price" in offers:
                        p_val = offers["price"]
                        if p_val:
                            p_int = int(float(p_val))
                            price = f"{p_int:,}" if p_int > 0 else str(p_val)

                            desc = ld_data.get("description", "")
                            m_orig = re.search(r"for\s+Rs\.?\s*([\d.]+)", desc, re.IGNORECASE)
                            if m_orig:
                                o_int = int(float(m_orig.group(1)))
                                original_price = f"{o_int:,}" if o_int > 0 else str(m_orig.group(1))

                    if "aggregateRating" in ld_data and isinstance(ld_data["aggregateRating"], dict):
                        r_val = ld_data["aggregateRating"].get("ratingValue")
                        if r_val:
                            rating = str(r_val)
                    break
            except Exception:
                pass

    main_price_elem = None
    if price == "N/A":
        # Strategy 1: Look for v1zwn21l with v1zwn20 (main detail price)
        for elem in soup.find_all("div", class_=lambda c: c and "v1zwn21l" in c and "v1zwn20" in c):
            txt = elem.get_text(strip=True)
            if re.match(r"^[\u20b9₹]?[\d,]{3,7}$", txt) and len(txt) < 12:
                price = txt
                main_price_elem = elem
                break

    if price == "N/A":
        # Strategy 2: Look for price inside css-g5y9jx parent that contains v1zwn21l
        for elem in soup.find_all("div", class_=lambda c: c and "css-g5y9jx" in c):
            inner = elem.find("div", class_=lambda c: c and "v1zwn21l" in c)
            if inner:
                txt = inner.get_text(strip=True)
                if re.match(r"^[\u20b9₹]?[\d,]{3,7}$", txt) and len(txt) < 12:
                    price = txt
                    main_price_elem = inner
                    break

    if price == "N/A":
        # Strategy 3: Any v1zwn21l div (skip variant v1zwn2d)
        for elem in soup.find_all("div", class_=lambda c: c and "v1zwn21l" in c):
            classes = " ".join(elem.get("class", []))
            if "v1zwn2d" in classes:
                continue
            txt = elem.get_text(strip=True)
            if re.match(r"^[\u20b9₹]?[\d,]{3,7}$", txt) and len(txt) < 12:
                price = txt
                main_price_elem = elem
                break

    # Original price (MRP): Find v1zwn21m near main price element
    if original_price == "N/A" and main_price_elem:
        price_container = main_price_elem.parent
        for _ in range(4):
            if price_container:
                orig_el = price_container.find("div", class_=lambda c: c and "v1zwn21m" in c)
                if orig_el:
                    txt = orig_el.get_text(strip=True)
                    if re.match(r"^[\u20b9₹]?[\d,]{3,7}$", txt) and len(txt) < 12:
                        original_price = txt
                        break
                price_container = price_container.parent

    if original_price == "N/A":
        for elem in soup.find_all("div", class_=lambda c: c and "v1zwn21m" in c):
            txt = elem.get_text(strip=True)
            if re.match(r"^[\u20b9₹]?[\d,]{3,7}$", txt) and len(txt) < 12:
                original_price = txt
                break

    if original_price == "N/A":
        original_price = price

    if rating == "N/A":
        rating_elem = soup.find("div", class_=lambda c: c and ("XQBx0U" in c or "_3LWZlK" in c or "_5O7F6f" in c))
        rating = rating_elem.get_text(strip=True) if rating_elem else "N/A"

    # Availability
    avail_elem = soup.find("div", class_=lambda c: c and ("_16FRp0" in c or (c and "out-of-stock" in c.lower() if isinstance(c, str) else False)))
    availability = avail_elem.get_text(strip=True) if avail_elem else "In Stock"

    # Seller
    seller_elem = soup.find("div", class_=lambda c: c and ("_1RLvfY" in c or (c and "SellerDetails" in c if isinstance(c, str) else False)))
    seller = seller_elem.get_text(strip=True) if seller_elem else "Flipkart Seller"

    # Clean price strings
    price_clean = clean_currency_symbol(price)
    orig_price_clean = clean_currency_symbol(original_price)

    # Extract numeric prices
    price_num, currency = extract_numeric_price(price_clean, default_currency="INR")
    orig_price_num, _ = extract_numeric_price(orig_price_clean, default_currency="INR")

    # Record and generate baseline
    if price_num:
        record_price_snapshot(pid, "IN", title or "", price_num, orig_price_num or price_num, "INR")
        generate_baseline_history_if_needed(
            asin=pid,
            country_code="IN",
            current_price=price_num,
            original_price=orig_price_num or price_num,
            currency="INR",
            title=title or ""
        )

    # Fetch price history timeline
    days = int(data.get("days", 90))
    history_report = get_price_history_analytics(pid, country_code="IN", days=days)

    current_num = price_num
    orig_num = orig_price_num or price_num

    savings_amount = round((orig_num - current_num), 2) if (orig_num and current_num and orig_num > current_num) else 0.0
    savings_pct = round((savings_amount / orig_num) * 100, 2) if (orig_num and savings_amount > 0) else 0.0

    target_met = False
    status = "REGULAR_PRICE"
    if current_num:
        if orig_num and orig_num > current_num:
            status = "DEAL_ACTIVE"
        if target_price is not None:
            try:
                if current_num <= float(target_price):
                    target_met = True
                    status = "TARGET_PRICE_MET"
                else:
                    status = "ABOVE_TARGET"
            except (ValueError, TypeError):
                pass

    return {
        "platform": "flipkart",
        "product_id": pid,
        "asin": pid,
        "title": title,
        "country_code": "IN",
        "currency": "INR",
        "current_price": price_clean,
        "current_price_numeric": current_num,
        "original_price": orig_price_clean,
        "original_price_numeric": orig_num,
        "savings_amount": savings_amount,
        "savings_percentage": savings_pct,
        "is_deal": bool(savings_amount > 0),
        "rating": rating,
        "target_price": target_price,
        "target_price_met": target_met,
        "price_status": status,
        "availability": availability,
        "seller": seller,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "product_url": product_url,
        "lowest_price_ever": history_report.get("lowest_price_ever"),
        "highest_price_ever": history_report.get("highest_price_ever"),
        "average_price": history_report.get("average_price"),
        "price_trend": history_report.get("price_trend"),
        "price_history": history_report.get("history", [])
    }


# Environmental control for browser mode & proxy configuration
is_headless = os.getenv("HEADLESS", "true").lower() == "true"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
PROXY_URL = os.getenv("PROXY_URL")


@browser(
    headless=is_headless,
    window_size=(1920, 1080),
    user_agent=USER_AGENT,
    proxy=PROXY_URL,
    block_images=False,
    reuse_driver=False,
    wait_for_complete_page_load=False,
)
def scrape_amazon_products(driver: Driver, data: dict) -> list[dict]:
    return _scrape_products_internal(driver, data)


@browser(
    headless=is_headless,
    window_size=(1920, 1080),
    user_agent=USER_AGENT,
    proxy=PROXY_URL,
    block_images=False,
    reuse_driver=False,
    wait_for_complete_page_load=False,
)
def scrape_flipkart_products(driver: Driver, data: dict) -> list[dict]:
    query = data.get("query") or data.get("q", "laptop")
    start_page = int(data.get("page") or data.get("start_page") or 1)
    raw_max = data.get("max_pages") or data.get("max_page") or start_page
    max_pages = min(max(int(raw_max), start_page), 50)

    all_products = []
    seen_ids = set()

    for page in range(start_page, max_pages + 1):
        target_url = f"https://www.flipkart.com/search?q={query}&page={page}"
        logger.info(f"Navigating to Flipkart Page {page}/{max_pages}: {target_url}")

        driver.get(target_url)
        driver.sleep(random.uniform(2.5, 3.5))

        products = parse_flipkart_grid(driver.page_html)

        new_products = []
        for p in products:
            pid = p.get("product_id") or p.get("asin")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                new_products.append(p)

        logger.info(f"Flipkart Page {page}: Extracted {len(new_products)} NEW unique products.")
        all_products.extend(new_products)

        if not new_products:
            break

    return all_products


@browser(
    headless=is_headless,
    window_size=(1920, 1080),
    user_agent=USER_AGENT,
    proxy=PROXY_URL,
    block_images=False,
    reuse_driver=False,
    wait_for_complete_page_load=False,
)
def scrape_amazon_deals(driver: Driver, data: dict) -> list[dict]:
    data["is_deals_only"] = True
    return _scrape_products_internal(driver, data)


@browser(
    headless=is_headless,
    window_size=(1920, 1080),
    user_agent=USER_AGENT,
    proxy=PROXY_URL,
    block_images=False,
    reuse_driver=False,
    wait_for_complete_page_load=False,
)
def scrape_amazon_bestsellers(driver: Driver, data: dict) -> list[dict]:
    category_node = data.get("category", "bestsellers").strip()
    country_code = data.get("country_code", "IN")
    _, base_domain = get_amazon_base_url(country_code)

    target_url = f"{base_domain}/gp/bestsellers/{category_node}" if category_node != "bestsellers" else f"{base_domain}/gp/bestsellers"
    logger.info(f"Navigating to Bestsellers Chart: {target_url}")

    driver.get(target_url)
    driver.sleep(random.uniform(2.0, 3.0))

    return parse_bestsellers_grid(driver.page_html, base_domain=base_domain, country_code=country_code)


@browser(
    headless=is_headless,
    window_size=(1920, 1080),
    user_agent=USER_AGENT,
    proxy=PROXY_URL,
    block_images=False,
    reuse_driver=False,
    wait_for_complete_page_load=False,
)
def scrape_amazon_product_details(driver: Driver, data: dict) -> dict:
    asin = data.get("asin", "").strip()
    country_code = data.get("country_code", "IN")
    _, base_domain = get_amazon_base_url(country_code)

    target_url = data.get("url")
    if not target_url:
        target_url = f"{base_domain}/dp/{asin}"

    logger.info(f"Navigating to Product Page: {target_url}")
    driver.get(target_url)
    driver.sleep(random.uniform(1.5, 2.5))

    return parse_product_details(driver.page_html, asin=asin, base_domain=base_domain, country_code=country_code)


@browser(
    headless=is_headless,
    window_size=(1920, 1080),
    user_agent=USER_AGENT,
    proxy=PROXY_URL,
    block_images=False,
    reuse_driver=False,
    wait_for_complete_page_load=False,
)
def track_amazon_price(driver: Driver, data: dict) -> dict:
    return _track_amazon_price_internal(driver, data)


@browser(
    headless=is_headless,
    window_size=(1920, 1080),
    user_agent=USER_AGENT,
    proxy=PROXY_URL,
    block_images=False,
    reuse_driver=False,
    wait_for_complete_page_load=False,
)
def track_flipkart_price(driver: Driver, data: dict) -> dict:
    return _track_flipkart_price_internal(driver, data)


@browser(
    headless=is_headless,
    window_size=(1920, 1080),
    user_agent=USER_AGENT,
    proxy=PROXY_URL,
    block_images=False,
    reuse_driver=False,
    wait_for_complete_page_load=False,
)
def fetch_historical_price_report(driver: Driver, data: dict) -> dict:
    asin = data.get("asin", "").strip()
    country_code = data.get("country_code", "IN")
    days = int(data.get("days", 90))

    platform = (data.get("platform") or "amazon").lower().strip()
    if platform == "flipkart":
        _track_flipkart_price_internal(driver, data)
    else:
        _track_amazon_price_internal(driver, data)

    return get_price_history_analytics(asin, country_code=country_code, days=days)


@browser(
    headless=is_headless,
    window_size=(1920, 1080),
    user_agent=USER_AGENT,
    proxy=PROXY_URL,
    block_images=False,
    reuse_driver=False,
    wait_for_complete_page_load=False,
)
def scrape_amazon_product_reviews(driver: Driver, data: dict) -> list[dict]:
    asin = data.get("asin", "").strip()
    country_code = data.get("country_code", "IN")
    _, base_domain = get_amazon_base_url(country_code)

    target_url = f"{base_domain}/dp/{asin}"
    logger.info(f"Navigating to Product Page for Reviews: {target_url}")

    driver.get(target_url)
    driver.sleep(random.uniform(1.5, 2.5))

    return parse_product_reviews(driver.page_html, asin=asin)
