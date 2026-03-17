"""
DealHunt Backend - FastAPI + Crawl4AI
Karthik Endluri | DealHunt Intelligence Engine
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timedelta
import asyncio
import json
import hashlib
import os
import logging

# ─── Crawl4AI imports ────────────────────────────────────────────────────────
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import LLMExtractionStrategy, JsonCssExtractionStrategy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dealhunt")

app = FastAPI(
    title="DealHunt Intelligence API",
    description="Crawl4AI-powered deal aggregation engine",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Pydantic schemas ─────────────────────────────────────────────────────────

class DealItem(BaseModel):
    product_name: str = Field(..., description="Full product name")
    brand: Optional[str] = Field(None, description="Brand or manufacturer")
    original_price: Optional[float] = Field(None, description="Original/MSRP price")
    sale_price: float = Field(..., description="Current sale price")
    discount_pct: Optional[float] = Field(None, description="Discount percentage")
    coupon_code: Optional[str] = Field(None, description="Coupon or promo code")
    retailer: str = Field(..., description="Store name")
    product_url: Optional[str] = Field(None, description="Direct product URL")
    image_url: Optional[str] = Field(None, description="Product image URL")
    rating: Optional[float] = Field(None, description="Product rating out of 5")
    review_count: Optional[int] = Field(None, description="Number of reviews")
    expiry_date: Optional[str] = Field(None, description="Deal expiration if known")
    in_stock: bool = Field(True, description="Whether item is in stock")
    deal_score: Optional[float] = Field(None, description="AI-computed deal quality 0-10")
    category: Optional[str] = Field(None, description="Product category")
    crawled_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

class DealSearchRequest(BaseModel):
    query: str = Field(..., description="Product search query")
    retailers: Optional[List[str]] = Field(
        default=["amazon", "target", "walmart", "bestbuy", "newegg"],
        description="Retailers to crawl"
    )
    max_results: int = Field(default=20, le=100)
    min_discount: Optional[float] = Field(None, description="Minimum discount %")

class PricePoint(BaseModel):
    price: float
    retailer: str
    timestamp: str

class DealSearchResponse(BaseModel):
    query: str
    total: int
    deals: List[DealItem]
    crawl_duration_ms: int
    retailers_crawled: List[str]
    timestamp: str

class WatchlistItem(BaseModel):
    product_name: str
    target_price: float
    current_price: float
    retailer: str
    product_url: str
    user_email: str

# ─── Retailer URL builders ────────────────────────────────────────────────────

RETAILER_URLS = {
    "amazon": lambda q: f"https://www.amazon.com/s?k={q.replace(' ', '+')}&s=price-asc-rank",
    "target": lambda q: f"https://www.target.com/s?searchTerm={q.replace(' ', '+')}",
    "walmart": lambda q: f"https://www.walmart.com/search?q={q.replace(' ', '+')}",
    "bestbuy": lambda q: f"https://www.bestbuy.com/site/searchpage.jsp?st={q.replace(' ', '+')}",
    "newegg": lambda q: f"https://www.newegg.com/p/pl?d={q.replace(' ', '+')}",
    "costco": lambda q: f"https://www.costco.com/CatalogSearch?keyword={q.replace(' ', '+')}",
    "homedepot": lambda q: f"https://www.homedepot.com/s/{q.replace(' ', '%20')}",
    "slickdeals": lambda q: f"https://slickdeals.net/newsearch.php?q={q.replace(' ', '+')}",
}

# ─── LLM Extraction strategy ──────────────────────────────────────────────────

def build_extraction_strategy(retailer: str) -> LLMExtractionStrategy:
    """Build Crawl4AI LLM extraction strategy for a retailer."""
    return LLMExtractionStrategy(
        provider="anthropic/claude-sonnet-4-20250514",
        api_token=os.getenv("ANTHROPIC_API_KEY"),
        schema=DealItem.model_json_schema(),
        extraction_type="schema",
        instruction=f"""
        Extract ALL product deals from this {retailer} search results page.
        For each product extract:
        - product_name: full name including model/specs
        - brand: manufacturer brand
        - sale_price: current selling price (required, numeric only, no $ sign)
        - original_price: crossed-out/MSRP price if shown
        - discount_pct: percentage off if shown (numeric only)
        - coupon_code: any visible promo/coupon codes
        - retailer: "{retailer}"
        - product_url: full URL to the product page
        - image_url: product image URL
        - rating: star rating (0-5 scale)
        - review_count: number of reviews
        - in_stock: true unless explicitly says out of stock
        - category: product category (Electronics, Home, Clothing, etc.)
        
        Return ONLY JSON array matching the schema. No markdown, no preamble.
        Focus on items with clear pricing. Skip ads and sponsored results.
        """,
        chunk_token_threshold=4000,
        overlap_rate=0.1,
        verbose=False
    )

# ─── Core crawl engine ────────────────────────────────────────────────────────

async def crawl_retailer(
    retailer: str,
    query: str,
    crawler: AsyncWebCrawler
) -> List[DealItem]:
    """Crawl a single retailer and extract deals."""
    if retailer not in RETAILER_URLS:
        logger.warning(f"Unknown retailer: {retailer}")
        return []

    url = RETAILER_URLS[retailer](query)
    logger.info(f"Crawling {retailer}: {url}")

    try:
        result = await crawler.arun(
            url=url,
            config=CrawlerRunConfig(
                extraction_strategy=build_extraction_strategy(retailer),
                cache_mode=CacheMode.BYPASS,
                wait_for="css:.s-result-item, .product-card, [data-testid='product-card']",
                js_code="""
                    // Scroll to load lazy-loaded items
                    window.scrollTo(0, document.body.scrollHeight / 2);
                    await new Promise(r => setTimeout(r, 1500));
                    window.scrollTo(0, document.body.scrollHeight);
                    await new Promise(r => setTimeout(r, 1000));
                """,
                page_timeout=30000,
                simulate_user=True,
                magic=True,  # Anti-bot bypass
            )
        )

        if not result.success:
            logger.error(f"{retailer} crawl failed: {result.error_message}")
            return []

        if not result.extracted_content:
            return []

        raw = json.loads(result.extracted_content)
        items = raw if isinstance(raw, list) else raw.get("items", [])

        deals = []
        for item in items:
            try:
                deal = DealItem(**item)
                deal.retailer = retailer
                # Compute deal score
                deal.deal_score = compute_deal_score(deal)
                deals.append(deal)
            except Exception as e:
                logger.debug(f"Skipping invalid item from {retailer}: {e}")

        logger.info(f"{retailer}: extracted {len(deals)} deals")
        return deals

    except Exception as e:
        logger.error(f"Error crawling {retailer}: {e}")
        return []


def compute_deal_score(deal: DealItem) -> float:
    """
    Score a deal 0-10 based on:
    - Discount percentage (40% weight)
    - Has coupon code (20% weight)
    - Rating quality (20% weight)
    - Review volume (20% weight)
    """
    score = 0.0

    # Discount score (max 4 pts)
    if deal.discount_pct:
        score += min(deal.discount_pct / 100 * 4, 4.0)
    elif deal.original_price and deal.sale_price < deal.original_price:
        discount = (deal.original_price - deal.sale_price) / deal.original_price
        score += min(discount * 4, 4.0)

    # Coupon bonus (max 2 pts)
    if deal.coupon_code:
        score += 2.0

    # Rating score (max 2 pts)
    if deal.rating:
        score += (deal.rating / 5.0) * 2

    # Review volume (max 2 pts) — log scale
    if deal.review_count:
        import math
        score += min(math.log10(max(deal.review_count, 1)) / 4 * 2, 2.0)

    return round(score, 2)


# ─── API endpoints ────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "DealHunt Intelligence API",
        "version": "2.0.0",
        "powered_by": "Crawl4AI + Claude",
        "endpoints": ["/search", "/crawl/deals", "/retailers", "/health"]
    }


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/retailers")
async def list_retailers():
    return {
        "retailers": list(RETAILER_URLS.keys()),
        "count": len(RETAILER_URLS)
    }


@app.post("/crawl/deals", response_model=DealSearchResponse)
async def crawl_deals(request: DealSearchRequest):
    """
    Main endpoint: crawl multiple retailers concurrently and return ranked deals.
    """
    start = datetime.utcnow()

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
        extra_args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]
    )

    all_deals: List[DealItem] = []
    crawled_retailers = []

    async with AsyncWebCrawler(config=browser_config) as crawler:
        # Fire all retailer crawls concurrently
        tasks = [
            crawl_retailer(retailer, request.query, crawler)
            for retailer in request.retailers
            if retailer in RETAILER_URLS
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for retailer, result in zip(request.retailers, results):
            if isinstance(result, Exception):
                logger.error(f"{retailer} failed: {result}")
            else:
                all_deals.extend(result)
                crawled_retailers.append(retailer)

    # Filter by min discount
    if request.min_discount:
        all_deals = [
            d for d in all_deals
            if d.discount_pct and d.discount_pct >= request.min_discount
        ]

    # Sort by deal score descending
    all_deals.sort(key=lambda d: d.deal_score or 0, reverse=True)

    # Limit results
    all_deals = all_deals[:request.max_results]

    duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)

    return DealSearchResponse(
        query=request.query,
        total=len(all_deals),
        deals=all_deals,
        crawl_duration_ms=duration_ms,
        retailers_crawled=crawled_retailers,
        timestamp=datetime.utcnow().isoformat()
    )


@app.get("/search")
async def search_deals(
    q: str = Query(..., description="Search query"),
    retailers: str = Query("amazon,walmart,target", description="Comma-separated retailers"),
    limit: int = Query(20, le=100),
    min_discount: Optional[float] = Query(None)
):
    """GET version of deal search — easier for frontend dev."""
    retailer_list = [r.strip() for r in retailers.split(",")]

    request = DealSearchRequest(
        query=q,
        retailers=retailer_list,
        max_results=limit,
        min_discount=min_discount
    )
    return await crawl_deals(request)


@app.post("/watchlist/add")
async def add_to_watchlist(item: WatchlistItem, background_tasks: BackgroundTasks):
    """Add item to price watchlist — background crawl alerts when price drops."""
    watch_id = hashlib.md5(f"{item.product_url}{item.user_email}".encode()).hexdigest()[:8]

    # In production: save to Postgres, schedule Celery task
    background_tasks.add_task(schedule_price_watch, item)

    return {
        "watch_id": watch_id,
        "message": f"Watching {item.product_name} — alert when below ${item.target_price}",
        "check_interval": "every 1 hour"
    }


async def schedule_price_watch(item: WatchlistItem):
    """Background task: periodically crawl product page for price changes."""
    logger.info(f"Price watch started for {item.product_name} @ ${item.target_price}")
    # In production: Celery beat + Postgres price_history table


@app.get("/price-history/{product_hash}")
async def get_price_history(product_hash: str):
    """Return price history for a tracked product."""
    # In production: query Postgres price_history table
    # Demo data for now
    now = datetime.utcnow()
    history = [
        {
            "price": 89.99 - i * 2.5,
            "retailer": "amazon",
            "timestamp": (now - timedelta(days=i * 7)).isoformat()
        }
        for i in range(8)
    ]
    return {
        "product_hash": product_hash,
        "history": history,
        "lowest_90_days": min(h["price"] for h in history),
        "current": history[0]["price"]
    }


@app.post("/crawl/coupons")
async def crawl_coupons(retailer: str = Query(...), product: str = Query(...)):
    """Crawl coupon sites for active promo codes for a specific retailer."""
    coupon_sources = [
        f"https://slickdeals.net/newsearch.php?q={retailer}+{product.replace(' ', '+')}",
        f"https://www.retailmenot.com/view/{retailer}.com",
    ]

    browser_config = BrowserConfig(headless=True, verbose=False)
    coupons = []

    async with AsyncWebCrawler(config=browser_config) as crawler:
        for url in coupon_sources:
            try:
                result = await crawler.arun(
                    url=url,
                    config=CrawlerRunConfig(
                        extraction_strategy=LLMExtractionStrategy(
                            provider="anthropic/claude-sonnet-4-20250514",
                            api_token=os.getenv("ANTHROPIC_API_KEY"),
                            instruction=f"""
                            Extract active coupon/promo codes for {retailer} related to {product}.
                            Return JSON array: [{{"code": "SAVE20", "discount": "20% off", "expiry": "2025-12-31", "verified": true}}]
                            Only include codes that appear active and valid. No markdown.
                            """,
                            extraction_type="block",
                        ),
                        cache_mode=CacheMode.BYPASS,
                    )
                )
                if result.success and result.extracted_content:
                    raw = json.loads(result.extracted_content)
                    if isinstance(raw, list):
                        coupons.extend(raw)
            except Exception as e:
                logger.error(f"Coupon crawl error: {e}")

    return {
        "retailer": retailer,
        "product": product,
        "coupons": coupons,
        "count": len(coupons)
    }
