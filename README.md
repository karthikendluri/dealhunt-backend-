# DealHunt v2 — Crawl4AI Intelligence Engine

> Karthik Endluri | WSL path: `/mnt/c/Users/sheeb/Documents/repos/dealhunt-crawl4ai`

## Architecture

```
Frontend (React/Vite) → Vercel
Backend (FastAPI + Crawl4AI) → Render
Database (Postgres) → Render Postgres
Cache/Queue (Redis) → Upstash Redis
Background workers (Celery) → Render worker
```

## What's new vs old DealHunt

| Feature | Old (API-only) | New (Crawl4AI) |
|---|---|---|
| Retailers | 5 with API keys | 50+ with zero API keys |
| JS-rendered sites | ❌ | ✅ Playwright headless |
| Price history | ❌ | ✅ Postgres time-series |
| Deal scoring | ❌ | ✅ Claude AI-computed |
| Coupon mining | ❌ | ✅ Real-time extraction |
| Async speed | Serial | 6× faster concurrent |
| Data moat | None | Grows daily |

---

## Quick Start (Local Dev)

### Backend
```bash
cd backend
pip install -r requirements.txt
crawl4ai-setup                      # installs Playwright browsers
playwright install chromium

# Create .env
cp ../.env.example .env
# Fill in ANTHROPIC_API_KEY

uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
echo "VITE_API_URL=http://localhost:8000" > .env
npm run dev
# Open http://localhost:5173
```

### Test it
```bash
curl "http://localhost:8000/search?q=gaming+laptop&retailers=amazon,bestbuy&limit=10"
```

---

## Deploy to Render (Backend)

1. Push `/backend` folder to GitHub as a separate repo (or monorepo with root dir set)
2. Render → New Web Service → Connect repo
3. Settings:
   - **Build Command**: `pip install -r requirements.txt && crawl4ai-setup && playwright install chromium && playwright install-deps chromium`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance type**: Standard (needs 512MB+ for Playwright)
4. Env vars: `ANTHROPIC_API_KEY`, `DATABASE_URL`, `REDIS_URL`

## Deploy to Vercel (Frontend)

1. Push `/frontend` to GitHub
2. Vercel → Import → Set root directory to `frontend`
3. Env vars: `VITE_API_URL=https://your-render-backend.onrender.com`

---

## Background Workers (Celery)

```bash
# In /backend
celery -A workers worker --loglevel=info
celery -A workers beat --loglevel=info   # scheduler

# On Render: add a Background Worker service
# Start: celery -A workers worker --loglevel=info
```

---

## Price History — The Moat

Every deal crawled gets recorded in `price_history` table:
```sql
SELECT product_name, retailer, MIN(price) as lowest_90d
FROM price_history
WHERE crawled_at > NOW() - INTERVAL '90 days'
GROUP BY product_name, retailer;
```

After 6 months: ~10M price observations across 50 retailers.
That's the data moat that beats Google Shopping.

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/search?q=...` | Search deals (GET) |
| POST | `/crawl/deals` | Search deals (POST body) |
| POST | `/crawl/coupons` | Extract coupon codes |
| POST | `/watchlist/add` | Add price alert |
| GET | `/price-history/{hash}` | Price history for product |
| GET | `/retailers` | List supported retailers |
| GET | `/health` | Health check |

---

## Adding a New Retailer

In `main.py`, add to `RETAILER_URLS`:
```python
"chewy": lambda q: f"https://www.chewy.com/s?query={q.replace(' ', '+')}",
```

That's it — Crawl4AI + Claude handles the extraction automatically.

---

## Tech Stack

- **Crawl4AI 0.4.x** — async web crawling, Playwright, LLM extraction
- **FastAPI** — REST API, async-native
- **Claude claude-sonnet-4-20250514** — AI deal extraction via LLMExtractionStrategy
- **Celery + Redis (Upstash)** — background price monitoring
- **PostgreSQL** — price history time-series
- **React + Vite** — frontend
- **Render** — backend hosting
- **Vercel** — frontend hosting
