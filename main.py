import os
import logging
import requests
from typing import Optional
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field
from fastapi import FastAPI, Query, Header, HTTPException, Security, Depends, Body
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from database import (
    ensure_default_api_key,
    validate_api_key,
    create_api_key,
    list_api_keys,
    revoke_api_key,
)
from scraper import (
    scrape_amazon_products,
    scrape_flipkart_products,
    scrape_amazon_deals,
    scrape_amazon_bestsellers,
    track_amazon_price,
    track_flipkart_price,
    fetch_historical_price_report,
    scrape_amazon_product_details,
    scrape_amazon_product_reviews,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("multi_scraper_api")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(
    x_api_key: Optional[str] = Security(api_key_header),
    api_key_query: Optional[str] = Query(None, alias="api_key")
):
    """
    Validates API key from either 'X-API-Key' HTTP Header or 'api_key' query parameter.
    Dynamically checks SQLite database for active keys.
    """
    provided_key = x_api_key or api_key_query
    if not provided_key or not validate_api_key(provided_key):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Invalid or missing API key. Please provide a valid key in 'X-API-Key' header or 'api_key' query parameter."
        )
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure at least one active master API key exists
    active_key = ensure_default_api_key()
    logger.info("=" * 60)
    logger.info(f"🔑 API Security Active! Active API Key: {active_key}")
    logger.info("=" * 60)
    yield


app = FastAPI(
    title="Multi-Platform E-Commerce Scraper & Price Tracker API",
    description="Unified API gateway for Amazon & Flipkart search, deals, category bestsellers, real-time price tracking, and historical timeline analytics.",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class UnifiedRequestPayload(BaseModel):
    action: str = Field(..., description="Action: search, scrape, deals, bestsellers, product, reviews, track_price, price_history, price_webhook")
    platform: Optional[str] = Field("amazon", description="E-commerce platform: 'amazon' or 'flipkart'")
    q: Optional[str] = Field(None, description="Search keyword or category")
    asin: Optional[str] = Field(None, description="Product ASIN/ID code (or Flipkart PID)")
    country_code: Optional[str] = Field("IN", description="Country code (IN, US, UK, etc.)")
    sort: Optional[str] = Field(None, description="Amazon sort order")
    tags: Optional[str] = Field(None, description="Amazon 'rh' refinement tag string")
    low_price: Optional[int] = Field(None, description="Minimum price filter bound")
    high_price: Optional[int] = Field(None, description="Maximum price filter bound")
    min_discount: Optional[int] = Field(None, description="Minimum discount percentage filter")
    page: Optional[int] = Field(None, description="Target page number to fetch (e.g. 1, 2, 3)")
    max_pages: Optional[int] = Field(None, description="Number of search/deal pages to scrape (1 to 50)")
    max_page: Optional[int] = Field(None, description="Alias for max_pages")
    target_price: Optional[float] = Field(None, description="Target price threshold for tracking/webhook")
    webhook_url: Optional[str] = Field(None, description="Webhook callback URL for price drop alerts")
    days: Optional[int] = Field(90, description="Historical lookback window in days (7 to 365)")


def execute_action(payload: dict) -> dict:
    """
    Central dispatcher executing requested multi-platform scraping & tracking actions.
    """
    action = payload.get("action", "").lower().strip()
    platform = payload.get("platform", "amazon").lower().strip()

    page_val = payload.get("page")
    if page_val:
        payload["page"] = page_val
        payload["start_page"] = page_val
        if not payload.get("max_pages"):
            payload["max_pages"] = page_val
    else:
        pages_count = payload.get("max_pages") or payload.get("max_page") or 1
        payload["max_pages"] = pages_count

    if action in ["search", "scrape"]:
        q = payload.get("q") or payload.get("query")
        if not q:
            raise HTTPException(status_code=400, detail="Missing required parameter 'q' for search action.")

        payload["q"] = q
        payload["query"] = q

        if platform == "flipkart":
            results = scrape_flipkart_products(payload)
        else:
            results = scrape_amazon_products(payload)

        return {
            "status": "success",
            "action": "search",
            "platform": platform,
            "page": payload.get("page", 1),
            "count": len(results),
            "data": results
        }

    elif action == "deals":
        q = payload.get("q", "all")
        payload["query"] = q

        if platform == "flipkart":
            results = scrape_flipkart_products(payload)
        else:
            results = scrape_amazon_deals(payload)

        return {
            "status": "success",
            "action": "deals",
            "platform": platform,
            "page": payload.get("page", 1),
            "count": len(results),
            "data": results
        }

    elif action == "bestsellers":
        if platform == "flipkart":
            q = payload.get("q") or payload.get("category") or "electronics"
            payload["query"] = q
            results = scrape_flipkart_products(payload)
        else:
            category = payload.get("q") or payload.get("category") or "bestsellers"
            payload["category"] = category
            results = scrape_amazon_bestsellers(payload)

        return {
            "status": "success",
            "action": "bestsellers",
            "platform": platform,
            "page": payload.get("page", 1),
            "count": len(results),
            "data": results
        }

    elif action == "product":
        asin = payload.get("asin")
        if not asin:
            raise HTTPException(status_code=400, detail="Missing required parameter 'asin' for product detail action.")

        if platform == "flipkart":
            result = track_flipkart_price(payload)
        else:
            result = scrape_amazon_product_details(payload)

        return {
            "status": "success",
            "action": "product",
            "platform": platform,
            "data": result
        }

    elif action == "reviews":
        asin = payload.get("asin")
        if not asin:
            raise HTTPException(status_code=400, detail="Missing required parameter 'asin' for product reviews action.")
        reviews = scrape_amazon_product_reviews(payload)
        return {
            "status": "success",
            "action": "reviews",
            "platform": platform,
            "count": len(reviews),
            "data": reviews
        }

    elif action in ["track_price", "price_tracker"]:
        asin = payload.get("asin")
        if not asin:
            raise HTTPException(status_code=400, detail="Missing required parameter 'asin' for price tracker action.")

        if platform == "flipkart":
            result = track_flipkart_price(payload)
        else:
            result = track_amazon_price(payload)

        return {
            "status": "success",
            "action": "track_price",
            "platform": platform,
            "data": result
        }

    elif action in ["price_history", "history"]:
        asin = payload.get("asin")
        if not asin:
            raise HTTPException(status_code=400, detail="Missing required parameter 'asin' for price history action.")

        report = fetch_historical_price_report(payload)
        return {
            "status": "success",
            "action": "price_history",
            "platform": platform,
            "data": report
        }

    elif action in ["price_webhook", "webhook"]:
        asin = payload.get("asin")
        webhook_url = payload.get("webhook_url")
        target_price = payload.get("target_price")

        if not asin or not target_price or not webhook_url:
            raise HTTPException(status_code=400, detail="Missing required parameters ('asin', 'target_price', 'webhook_url') for price webhook action.")

        if platform == "flipkart":
            result = track_flipkart_price(payload)
        else:
            result = track_amazon_price(payload)

        is_met = result.get("target_price_met", False)
        webhook_sent = False

        if is_met and webhook_url:
            try:
                alert_payload = {
                    "event": "PRICE_DROP_ALERT",
                    "platform": platform,
                    "asin": asin,
                    "title": result.get("title"),
                    "current_price": result.get("current_price"),
                    "current_price_numeric": result.get("current_price_numeric"),
                    "target_price": target_price,
                    "product_url": result.get("product_url"),
                    "timestamp": result.get("timestamp")
                }
                resp = requests.post(webhook_url, json=alert_payload, timeout=5)
                webhook_sent = bool(resp.status_code < 300)
            except Exception as e:
                logger.error(f"Failed to post price drop webhook to {webhook_url}: {e}")

        return {
            "status": "success",
            "action": "price_webhook",
            "platform": platform,
            "webhook_triggered": is_met,
            "webhook_sent": webhook_sent,
            "data": result
        }

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported action '{action}'. Supported actions: search, scrape, deals, bestsellers, product, reviews, track_price, price_history, price_webhook."
        )


@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "multi-platform-scraper-service",
        "version": "2.0.0",
        "gateway": "/api/v1/execute"
    }


@app.get("/api/v1/execute", dependencies=[Depends(verify_api_key)])
def execute_api_get(
    action: str = Query(..., description="Action: search, scrape, deals, bestsellers, product, reviews, track_price, price_history, price_webhook"),
    platform: str = Query("amazon", description="E-commerce platform: 'amazon' or 'flipkart'"),
    q: Optional[str] = Query(None, description="Search keyword or category"),
    asin: Optional[str] = Query(None, description="Product ASIN/ID code (or Flipkart PID)"),
    country_code: str = Query("IN", description="Country code (IN, US, UK, etc.)"),
    sort: Optional[str] = Query(None, description="Amazon sort order"),
    tags: Optional[str] = Query(None, description="Amazon 'rh' refinement tag string"),
    low_price: Optional[int] = Query(None, description="Minimum price filter bound"),
    high_price: Optional[int] = Query(None, description="Maximum price filter bound"),
    min_discount: Optional[int] = Query(None, description="Minimum discount percentage filter"),
    page: Optional[int] = Query(None, description="Target page number to fetch (e.g. 1, 2, 3)"),
    max_pages: Optional[int] = Query(None, description="Number of search/deal pages to scrape (1 to 50)"),
    max_page: Optional[int] = Query(None, description="Alias for max_pages"),
    target_price: Optional[float] = Query(None, description="Target price threshold for tracking/webhook"),
    webhook_url: Optional[str] = Query(None, description="Webhook callback URL for price drop alerts"),
    days: int = Query(90, description="Historical lookback window in days (7 to 365)")
):
    payload = {
        "action": action,
        "platform": platform,
        "q": q,
        "asin": asin,
        "country_code": country_code,
        "sort": sort,
        "tags": tags,
        "low_price": low_price,
        "high_price": high_price,
        "min_discount": min_discount,
        "page": page,
        "max_pages": max_pages or max_page,
        "max_page": max_page,
        "target_price": target_price,
        "webhook_url": webhook_url,
        "days": days
    }
    return execute_action(payload)


@app.post("/api/v1/execute", dependencies=[Depends(verify_api_key)])
def execute_api_post(payload: UnifiedRequestPayload):
    return execute_action(payload.model_dump())


class KeyGeneratePayload(BaseModel):
    name: Optional[str] = Field("App Key", description="Name/description for this API key")
    custom_key: Optional[str] = Field(None, description="Optional custom API key string to register")


class KeyRevokePayload(BaseModel):
    api_key: str = Field(..., description="API key to deactivate")


@app.post("/api/v1/keys/generate", dependencies=[Depends(verify_api_key)], tags=["API Keys"])
def generate_key_endpoint(payload: KeyGeneratePayload):
    """
    Dynamically generates or registers a new active API key.
    Requires an existing valid API key in header or query parameter.
    """
    new_key = create_api_key(name=payload.name or "App Key", custom_key=payload.custom_key)
    return {
        "status": "success",
        "message": f"API key '{payload.name or 'App Key'}' created/activated successfully.",
        "api_key": new_key
    }


@app.get("/api/v1/keys/list", dependencies=[Depends(verify_api_key)], tags=["API Keys"])
def list_keys_endpoint():
    """
    Lists all active API keys in the database.
    """
    keys = list_api_keys()
    return {
        "status": "success",
        "count": len(keys),
        "keys": keys
    }


@app.post("/api/v1/keys/revoke", dependencies=[Depends(verify_api_key)], tags=["API Keys"])
def revoke_key_endpoint(payload: KeyRevokePayload):
    """
    Revokes (deactivates) an API key.
    """
    success = revoke_api_key(payload.api_key)
    if not success:
        raise HTTPException(status_code=404, detail="API key not found or already inactive.")
    return {
        "status": "success",
        "message": "API key revoked successfully."
    }

