"""
DealHunt Background Workers — Celery
Hourly price crawls, watchlist alerts, coupon refresh
"""

from celery import Celery
from celery.schedules import crontab
import asyncio
import os
import logging

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "dealhunt",
    broker=REDIS_URL,
    backend=REDIS_URL
)

celery_app.conf.beat_schedule = {
    # Crawl trending deals every 2 hours
    "crawl-trending": {
        "task": "workers.crawl_trending_deals",
        "schedule": crontab(minute=0, hour="*/2"),
    },
    # Check all watchlist items every hour
    "check-watchlist": {
        "task": "workers.check_watchlist_prices",
        "schedule": crontab(minute=30),
    },
    # Refresh coupon cache every 6 hours
    "refresh-coupons": {
        "task": "workers.refresh_coupon_cache",
        "schedule": crontab(minute=0, hour="*/6"),
    },
}

celery_app.conf.timezone = "UTC"

TRENDING_QUERIES = [
    "laptop deals", "TV sale", "headphones discount",
    "gaming deals", "phone deals", "tablet sale",
    "kitchen appliances", "outdoor furniture", "running shoes",
]


@celery_app.task(name="workers.crawl_trending_deals")
def crawl_trending_deals():
    """Background crawl for trending deal categories."""
    from main import crawl_deals, DealSearchRequest
    from models import get_db, record_price
    import hashlib

    logging.info("Starting trending deals crawl...")

    for query in TRENDING_QUERIES:
        try:
            request = DealSearchRequest(
                query=query,
                retailers=["amazon", "walmart", "target", "bestbuy"],
                max_results=30
            )
            response = asyncio.run(crawl_deals(request))

            db = next(get_db())
            for deal in response.deals:
                product_hash = hashlib.md5(
                    f"{deal.product_name}{deal.retailer}".encode()
                ).hexdigest()

                record_price(
                    db=db,
                    product_hash=product_hash,
                    product_name=deal.product_name,
                    retailer=deal.retailer,
                    price=deal.sale_price,
                    original_price=deal.original_price,
                    product_url=deal.product_url
                )

            logging.info(f"Recorded {len(response.deals)} deals for '{query}'")

        except Exception as e:
            logging.error(f"Error crawling '{query}': {e}")


@celery_app.task(name="workers.check_watchlist_prices")
def check_watchlist_prices():
    """Check watchlist items and send alerts if price drops below target."""
    from models import get_db, WatchlistEntry, record_price
    import hashlib

    db = next(get_db())
    items = db.query(WatchlistEntry).filter(WatchlistEntry.alert_sent == False).all()

    logging.info(f"Checking {len(items)} watchlist items...")

    for item in items:
        try:
            # In production: crawl item.product_url directly for exact price
            # Here we use the deal search
            from main import crawl_deals, DealSearchRequest
            request = DealSearchRequest(
                query=item.product_name,
                retailers=[item.retailer],
                max_results=5
            )
            response = asyncio.run(crawl_deals(request))

            if response.deals:
                current_price = response.deals[0].sale_price
                item.current_price = current_price

                if current_price <= item.target_price:
                    send_price_alert(item, current_price)
                    item.alert_sent = True
                    logging.info(f"Alert sent: {item.product_name} dropped to ${current_price}")

                db.commit()

        except Exception as e:
            logging.error(f"Watchlist check error for {item.id}: {e}")


def send_price_alert(item, current_price: float):
    """Send email/push notification for price drop."""
    # In production: use SendGrid / AWS SES / Firebase Cloud Messaging
    logging.info(
        f"PRICE ALERT: {item.product_name} is now ${current_price} "
        f"(target: ${item.target_price}) at {item.retailer} — {item.user_email}"
    )


@celery_app.task(name="workers.refresh_coupon_cache")
def refresh_coupon_cache():
    """Refresh coupon database for top retailers."""
    retailers = ["amazon", "walmart", "target", "bestbuy", "homedepot"]
    logging.info(f"Refreshing coupons for {len(retailers)} retailers...")

    for retailer in retailers:
        try:
            from main import crawl_coupons
            result = asyncio.run(crawl_coupons(retailer=retailer, product=""))
            logging.info(f"{retailer}: found {result['count']} coupons")
        except Exception as e:
            logging.error(f"Coupon refresh error for {retailer}: {e}")
