# 🛒 Multi-Platform E-Commerce Scraper & Price Tracker API

A production-ready, high-performance Python microservice built with **FastAPI**, **Botasaurus**, and **SQLite**. Designed for high-concurrency e-commerce data extraction, deal discovery, real-time price tracking, historical price trend analytics, price-drop webhook notifications, and dynamic API key security across **Amazon** (multi-country) and **Flipkart**.

---

## 🌟 Key Features

- **Dynamic API Key Security (`ws_live_...`)**: Strict API authentication powered by SQLite. Supports auto-generated master keys, environment variable keys, and dynamic key management endpoints.
- **Unified API Gateway (`/api/v1/execute`)**: Accepts both `GET` query parameters and `POST` JSON payloads.
- **Multi-Platform Support**: Works seamlessly across **Amazon** (`amazon.in`, `amazon.com`, `amazon.co.uk`, `amazon.ca`, etc.) and **Flipkart** (`flipkart.com`).
- **Clean Price Output**: Currency symbols (`₹`, `$`, `£`, `€`) are automatically stripped from string price outputs so your frontend can render custom currency formatting.
- **Authoritative Data Extraction**: Combines JSON-LD structured schema parsing with fallback DOM selectors for exact price, discount, ratings, and MRP extraction.
- **Pagination & Deduplication**: Multi-page extraction support with global deduplication.
- **Price History Timeline & Analytics**: Built-in SQLite database tracking lowest ever price, highest ever price, average price, and price trend (`PRICE_DROPPING`, `PRICE_RISING`, `STABLE`).
- **Automated Price Drop Alerts**: Trigger webhooks automatically when a tracked item hits or drops below a target price.
- **Docker & Cloud Ready**: Fully containerized with headless Chromium and ready for deployment on **Railway**, **Render**, **AWS**, or **Docker Engine**.

---

## 🔐 API Security & How to Get an API Key

All API endpoints are strictly secured. Every request must include a valid active API key starting with **`ws_live_`** (Web Scraper Live).

### 1. How to Get / Obtain Your API Key

#### Method A: Automatic Master Key (On Server Startup)
When you start the server for the first time, FastAPI automatically initializes an active Master API Key and prints it to your server console logs:

```text
============================================================
🔑 API Security Active! Active API Key: ws_live_YOUR_API_KEY
============================================================
```

#### Method B: Environment Variable (`API_KEY`)
You can set your own custom master key by defining the `API_KEY` environment variable in your `.env` or deployment platform:

```env
API_KEY=my_secret_passcode_2026
```
*(The API will automatically prefix it as `ws_live_my_secret_passcode_2026` if no prefix is given).*

#### Method C: Dynamically Generate API Keys via Endpoint
Holding an existing valid API key lets you generate new API keys dynamically for different applications (e.g. Mobile App, Web Frontend):

```bash
curl -X POST "http://localhost:8000/api/v1/keys/generate" \
  -H "X-API-Key: ws_live_YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Frontend Web App",
    "custom_key": "ws_live_frontend_app_999"
  }'
```

---

### 2. Passing the API Key in Requests

You can pass your `ws_live_...` API key in either of two ways:

1. **HTTP Header (Recommended)**:
   ```http
   X-API-Key: ws_live_YOUR_API_KEY
   ```

2. **URL Query Parameter**:
   ```http
   ?api_key=ws_live_YOUR_API_KEY
   ```

---

## 🌐 Complete API Action Reference (`/api/v1/execute`)

### Endpoint Summary Table

| Action | Platforms | Description | Required Parameters |
|---|---|---|---|
| `search` / `scrape` | Amazon, Flipkart | Search products with filters & pagination | `q` |
| `deals` | Amazon, Flipkart | Extract active deals and discounted items | `q` (or defaults to `all`) |
| `bestsellers` | Amazon, Flipkart | Extract category bestsellers | `q` or `category` |
| `product` | Amazon, Flipkart | Fetch detailed product specification | `asin` / `product_id` |
| `reviews` | Amazon | Extract customer reviews | `asin` |
| `track_price` | Amazon, Flipkart | Real-time price tracking & baseline snapshot | `asin` / `product_id` |
| `price_history` | Amazon, Flipkart | Retrieve historical price analytics & timeline | `asin` / `product_id` |
| `price_webhook` | Amazon, Flipkart | Track price and trigger webhook if target is met | `asin`, `target_price`, `webhook_url` |

---

## 🔗 cURL Usage Examples for Every Case

*(Replace `ws_live_YOUR_KEY` with your actual API key)*

### Case 1: Search Products (`action=search`)

Search for products with query, country code, price bounds, sorting, and pagination.

#### Amazon Search Example
```bash
curl "http://localhost:8000/api/v1/execute?action=search&platform=amazon&q=wireless+keyboard&country_code=IN&max_pages=2&api_key=ws_live_YOUR_KEY"
```

#### Flipkart Search Example
```bash
curl "http://localhost:8000/api/v1/execute?action=search&platform=flipkart&q=smartwatch&max_pages=2&api_key=ws_live_YOUR_KEY"
```

---

### Case 2: Extract Deals (`action=deals`)

Fetch discounted products and deals matching a minimum discount threshold.

#### Amazon Deals Example
```bash
curl "http://localhost:8000/api/v1/execute?action=deals&platform=amazon&q=headphones&country_code=IN&min_discount=20&api_key=ws_live_YOUR_KEY"
```

#### Flipkart Deals Example
```bash
curl "http://localhost:8000/api/v1/execute?action=deals&platform=flipkart&q=laptop&min_discount=15&api_key=ws_live_YOUR_KEY"
```

---

### Case 3: Category Bestsellers (`action=bestsellers`)

Extract top-selling products in a category.

#### Amazon Bestsellers Example
```bash
curl "http://localhost:8000/api/v1/execute?action=bestsellers&platform=amazon&category=electronics&country_code=IN&api_key=ws_live_YOUR_KEY"
```

#### Flipkart Bestsellers Example
```bash
curl "http://localhost:8000/api/v1/execute?action=bestsellers&platform=flipkart&category=mobiles&api_key=ws_live_YOUR_KEY"
```

---

### Case 4: Single Product Specification (`action=product`)

Extract complete details for a single product using ASIN or Product ID.

#### Amazon Product Details Example
```bash
curl "http://localhost:8000/api/v1/execute?action=product&platform=amazon&asin=B0DBPCSWX5&country_code=IN&api_key=ws_live_YOUR_KEY"
```

#### Flipkart Product Details Example
```bash
curl "http://localhost:8000/api/v1/execute?action=product&platform=flipkart&asin=SMWGEH7VNGPYN5NV&api_key=ws_live_YOUR_KEY"
```

---

### Case 5: Product Customer Reviews (`action=reviews`)

Extract customer reviews for an Amazon product.

#### Amazon Reviews Example
```bash
curl "http://localhost:8000/api/v1/execute?action=reviews&platform=amazon&asin=B0DBPCSWX5&country_code=IN&api_key=ws_live_YOUR_KEY"
```

---

### Case 6: Real-time Price Tracker (`action=track_price`)

Track current price, calculate discount/savings, update SQLite price history database, and evaluate target price.

#### Amazon Price Tracker Example
```bash
curl "http://localhost:8000/api/v1/execute?action=track_price&platform=amazon&asin=B0DBPCSWX5&country_code=IN&target_price=700&api_key=ws_live_YOUR_KEY"
```

#### Flipkart Price Tracker Example (by PID)
```bash
curl "http://localhost:8000/api/v1/execute?action=track_price&platform=flipkart&asin=SMWGEH7VNGPYN5NV&target_price=2000&api_key=ws_live_YOUR_KEY"
```

#### Flipkart Price Tracker Example (by Product URL)
```bash
curl "http://localhost:8000/api/v1/execute?action=track_price&platform=flipkart&asin=https://www.flipkart.com/noise-icon-2-1-8-display-bluetooth-calling-women-s-edition-ai-voice-assistant-smartwatch/p/itm968c523d99eae?pid=SMWGEH7VNGPYN5NV&target_price=2000&api_key=ws_live_YOUR_KEY"
```

---

### Case 7: Price History Analytics (`action=price_history`)

Retrieve historical price metrics (`lowest_price_ever`, `highest_price_ever`, `average_price`, `price_trend`, timeline array).

#### Price History Example
```bash
curl "http://localhost:8000/api/v1/execute?action=price_history&platform=flipkart&asin=SMWGEH7VNGPYN5NV&days=90&api_key=ws_live_YOUR_KEY"
```

---

### Case 8: Automated Price Drop Webhook (`action=price_webhook`)

Trigger an alert payload to a custom webhook URL when a target price condition is met.

#### POST Request Example
```bash
curl -X POST "http://localhost:8000/api/v1/execute" \
  -H "X-API-Key: ws_live_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "price_webhook",
    "platform": "flipkart",
    "asin": "SMWGEH7VNGPYN5NV",
    "target_price": 2000,
    "webhook_url": "https://your-domain.com/api/webhooks/price-alert"
  }'
```

---

### API Key Management Endpoints

#### Generate / Register Key (`POST /api/v1/keys/generate`)
```bash
curl -X POST "http://localhost:8000/api/v1/keys/generate" \
  -H "X-API-Key: ws_live_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Mobile App Client",
    "custom_key": "ws_live_mobile_app_123"
  }'
```

#### List Active Keys (`GET /api/v1/keys/list`)
```bash
curl "http://localhost:8000/api/v1/keys/list" \
  -H "X-API-Key: ws_live_YOUR_KEY"
```

#### Revoke Key (`POST /api/v1/keys/revoke`)
```bash
curl -X POST "http://localhost:8000/api/v1/keys/revoke" \
  -H "X-API-Key: ws_live_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "ws_live_mobile_app_123"
  }'
```

---

## 🛠️ Local Development & Setup

### Prerequisites
- **Python 3.10+**
- **Google Chrome / Chromium** installed on system

### Installation Steps

1. **Clone the repository**:
   ```bash
   git clone https://github.com/gvraghuveer/web-scrapper.git
   cd web-scrapper
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv venv
   # Linux/macOS:
   source venv/bin/activate
   # Windows:
   venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the API server**:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

5. **Access interactive Swagger UI documentation**:
   Open `http://localhost:8000/docs` in your browser.

---

## 🐳 Cloud & Docker Deployment

### Railway Deployment (Recommended)
1. Push code to your GitHub repository.
2. Sign in to [Railway.app](https://railway.app) and click **"+ New Project"** ➔ **"Deploy from GitHub repo"**.
3. Select your repository. Railway automatically reads the `Dockerfile` and builds the environment.
4. Go to **Settings** ➔ **Networking** ➔ Click **"Generate Domain"** to get your public API URL.

### Render Deployment
1. Sign in to [Render.com](https://render.com) and create a **New Web Service**.
2. Connect your GitHub repository.
3. Select **Docker** as the environment and click **Deploy**.

---

## 📄 License

Distributed under the **MIT License**.
