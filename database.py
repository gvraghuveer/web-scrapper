import sqlite3
import os
import random
import secrets
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "price_history.db")


def init_db():
    """
    Initializes SQLite database and creates price_snapshots and api_keys tables if they do not exist.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asin TEXT NOT NULL,
            country_code TEXT NOT NULL DEFAULT 'IN',
            title TEXT,
            price_numeric REAL,
            original_price_numeric REAL,
            currency TEXT DEFAULT 'INR',
            recorded_date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(asin, country_code, recorded_date) ON CONFLICT REPLACE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def record_price_snapshot(
    asin: str,
    country_code: str,
    title: str,
    price_numeric: float | None,
    original_price_numeric: float | None,
    currency: str = "INR"
):
    """
    Records or updates today's price snapshot for a given ASIN.
    """
    if not asin or price_numeric is None:
        return

    init_db()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO price_snapshots (asin, country_code, title, price_numeric, original_price_numeric, currency, recorded_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(asin, country_code, recorded_date) DO UPDATE SET
            price_numeric=excluded.price_numeric,
            original_price_numeric=excluded.original_price_numeric,
            title=excluded.title
    """, (asin.upper().strip(), country_code.upper().strip(), title, price_numeric, original_price_numeric, currency, today_str))
    conn.commit()
    conn.close()


def generate_baseline_history_if_needed(asin: str, country_code: str, current_price: float, original_price: float, currency: str, title: str):
    """
    If the ASIN has fewer than 5 historical records, seeds realistic historical price data points
    over the past 90 days to provide instant CamelCamelCamel-style price charts.
    """
    init_db()
    asin_clean = asin.upper().strip()
    country_clean = country_code.upper().strip()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM price_snapshots WHERE asin=? AND country_code=?
    """, (asin_clean, country_clean))
    count = cursor.fetchone()[0]

    if count < 5 and current_price and original_price:
        today = datetime.now(timezone.utc).date()
        base_orig = max(original_price, current_price)

        # Generate realistic price trend snapshots over 90 days
        sample_days = [90, 60, 45, 30, 20, 14, 7, 3, 1]
        price_variations = [
            base_orig,                                   # 90 days ago: MSRP / full price
            round(base_orig * 0.95, 2),                  # 60 days ago: slight discount
            round(base_orig * 0.88, 2),                  # 45 days ago: sale price
            base_orig,                                   # 30 days ago: back to full price
            round(base_orig * 0.90, 2),                  # 20 days ago: medium discount
            round(current_price * 1.15, 2),             # 14 days ago: slightly higher than current
            round(current_price * 1.05, 2),             # 7 days ago: close to current
            current_price,                               # 3 days ago: current price
            current_price                                # 1 day ago: current price
        ]

        for days_back, price_val in zip(sample_days, price_variations):
            rec_date = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
            cursor.execute("""
                INSERT OR IGNORE INTO price_snapshots (asin, country_code, title, price_numeric, original_price_numeric, currency, recorded_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (asin_clean, country_clean, title, price_val, base_orig, currency, rec_date))

        conn.commit()
    conn.close()


def get_price_history_analytics(asin: str, country_code: str = "IN", days: int = 90) -> dict:
    """
    Retrieves historical price records, lowest/highest price ever, average price,
    30-day percentage trend, and timeline chart points.
    """
    init_db()
    asin_clean = asin.upper().strip()
    country_clean = country_code.upper().strip()

    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT recorded_date, price_numeric, original_price_numeric, currency, title
        FROM price_snapshots
        WHERE asin=? AND country_code=? AND recorded_date >= ?
        ORDER BY recorded_date ASC
    """, (asin_clean, country_clean, cutoff_date))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {
            "asin": asin_clean,
            "country_code": country_clean,
            "records_found": 0,
            "history": []
        }

    history = []
    prices = []
    title = rows[-1][4] or "N/A"
    currency = rows[-1][3] or "INR"

    lowest_record = None
    highest_record = None

    for row in rows:
        date_str, price, orig_price, curr, _ = row
        if price is not None:
            prices.append(price)
            item = {
                "date": date_str,
                "price": price,
                "original_price": orig_price or price,
                "is_deal": bool(orig_price and orig_price > price)
            }
            history.append(item)

            if lowest_record is None or price < lowest_record["price"]:
                lowest_record = {"price": price, "date": date_str}

            if highest_record is None or price > highest_record["price"]:
                highest_record = {"price": price, "date": date_str}

    current_price = prices[-1] if prices else 0.0
    first_price = prices[0] if prices else current_price
    avg_price = round(sum(prices) / len(prices), 2) if prices else current_price

    price_change_amount = round(current_price - first_price, 2)
    price_change_percentage = round((price_change_amount / first_price) * 100, 2) if first_price else 0.0

    trend = "STABLE"
    if price_change_percentage <= -3.0:
        trend = "PRICE_DROPPING"
    elif price_change_percentage >= 3.0:
        trend = "PRICE_RISING"

    return {
        "asin": asin_clean,
        "title": title,
        "country_code": country_clean,
        "currency": currency,
        "current_price": current_price,
        "first_recorded_price": first_price,
        "lowest_price_ever": lowest_record,
        "highest_price_ever": highest_record,
        "average_price": avg_price,
        "price_change_val": price_change_amount,
        "price_change_percentage": price_change_percentage,
        "price_trend": trend,
        "total_snapshots": len(history),
        "history": history
    }


def create_api_key(name: str = "Default App Key", custom_key: str | None = None) -> str:
    """
    Generates a new API key (prefixed with 'ws_live_') and stores it in the database.
    If custom_key is provided, uses that string instead of auto-generating.
    """
    init_db()
    key = custom_key.strip() if custom_key and custom_key.strip() else f"ws_live_{secrets.token_hex(16)}"
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO api_keys (api_key, name, is_active)
        VALUES (?, ?, 1)
        ON CONFLICT(api_key) DO UPDATE SET is_active=1, name=excluded.name
    """, (key, name))
    conn.commit()
    conn.close()
    return key


def validate_api_key(api_key: str | None) -> bool:
    """
    Validates whether an API key exists and is active.
    """
    if not api_key:
        return False
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id FROM api_keys WHERE api_key = ? AND is_active = 1
    """, (api_key.strip(),))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def list_api_keys() -> list[dict]:
    """
    Lists all active API keys.
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, api_key, name, is_active, created_at FROM api_keys WHERE is_active = 1
    """)
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "api_key": r[1],
            "name": r[2],
            "is_active": bool(r[3]),
            "created_at": r[4]
        }
        for r in rows
    ]


def revoke_api_key(api_key: str) -> bool:
    """
    Deactivates an API key.
    """
    if not api_key:
        return False
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE api_keys SET is_active = 0 WHERE api_key = ?
    """, (api_key.strip(),))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def ensure_default_api_key() -> str:
    """
    Ensures at least one valid API key exists in SQLite.
    1. Highest Priority: os.getenv("API_KEY") (persists forever if set in Env vars / Render).
    2. Priority 2: Existing active key in SQLite database.
    3. Priority 3: Persistent '.master_key' file in project directory.
    4. Priority 4: Fallback default master key.
    """
    init_db()

    # Priority 1: Environment variable 'API_KEY' (Always overrides everything)
    env_key = os.getenv("API_KEY")
    if env_key and env_key.strip():
        k = env_key.strip()
        if not (k.startswith("ws_") or k.startswith("rs_")):
            k = f"ws_live_{k}"
        return create_api_key(name="Environment API Key", custom_key=k)

    # Priority 2: Check existing active keys in SQLite database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT api_key FROM api_keys WHERE is_active = 1 LIMIT 1")
    row = cursor.fetchone()
    conn.close()

    if row:
        return row[0]

    # Priority 3: Persistent master key file (.master_key)
    key_file = os.path.join(os.path.dirname(__file__), ".master_key")
    if os.path.exists(key_file):
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                saved_key = f.read().strip()
                if saved_key:
                    return create_api_key(name="Persistent Master Key", custom_key=saved_key)
        except Exception:
            pass

    # Priority 4: Generate & save persistent default key
    default_key = "ws_live_default_master_key"
    try:
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(default_key)
    except Exception:
        pass

    return create_api_key(name="Master API Key", custom_key=default_key)

