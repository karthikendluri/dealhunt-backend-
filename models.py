"""
DealHunt Database Models
Price history tracking — the data moat that wins long-term
"""

from sqlalchemy import create_engine, Column, String, Float, Integer, Boolean, DateTime, Text, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/dealhunt")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class PriceHistory(Base):
    """Core price history table — this is the moat."""
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, index=True)
    product_hash = Column(String(64), index=True, nullable=False)
    product_name = Column(String(500))
    retailer = Column(String(100), index=True)
    price = Column(Float, nullable=False)
    original_price = Column(Float)
    discount_pct = Column(Float)
    product_url = Column(Text)
    in_stock = Column(Boolean, default=True)
    crawled_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("idx_product_retailer_date", "product_hash", "retailer", "crawled_at"),
    )


class WatchlistEntry(Base):
    """User price alerts."""
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String(200), index=True)
    product_hash = Column(String(64), index=True)
    product_name = Column(String(500))
    product_url = Column(Text)
    retailer = Column(String(100))
    target_price = Column(Float)
    current_price = Column(Float)
    alert_sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_checked = Column(DateTime)


class CouponCache(Base):
    """Cached coupon codes — refreshed every 6 hours."""
    __tablename__ = "coupon_cache"

    id = Column(Integer, primary_key=True)
    retailer = Column(String(100), index=True)
    code = Column(String(100))
    discount_description = Column(String(500))
    expiry_date = Column(DateTime)
    verified = Column(Boolean, default=False)
    cached_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def record_price(db, product_hash: str, product_name: str, retailer: str,
                  price: float, original_price: float = None,
                  product_url: str = None):
    """Record a price observation."""
    entry = PriceHistory(
        product_hash=product_hash,
        product_name=product_name,
        retailer=retailer,
        price=price,
        original_price=original_price,
        discount_pct=round((original_price - price) / original_price * 100, 1)
                     if original_price and original_price > price else None,
        product_url=product_url
    )
    db.add(entry)
    db.commit()
    return entry


def get_price_history(db, product_hash: str, days: int = 90):
    """Get price history for a product over last N days."""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    return (
        db.query(PriceHistory)
        .filter(PriceHistory.product_hash == product_hash)
        .filter(PriceHistory.crawled_at >= cutoff)
        .order_by(PriceHistory.crawled_at.desc())
        .all()
    )


def get_lowest_price(db, product_hash: str, days: int = 90) -> float:
    """Get the lowest recorded price in the last N days."""
    from sqlalchemy import func
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = (
        db.query(func.min(PriceHistory.price))
        .filter(PriceHistory.product_hash == product_hash)
        .filter(PriceHistory.crawled_at >= cutoff)
        .scalar()
    )
    return result
