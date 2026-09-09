"""
ORM models. Kept deliberately minimal — only what the MVP actually needs.
"""
from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, Text

from app.database import Base


class QueryLog(Base):
    """Every chat query, for debugging and for a 'recent queries' demo feature."""
    __tablename__ = "query_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_message = Column(Text, nullable=False)
    detected_language = Column(String(10), default="en")
    location_name = Column(String(120), nullable=True)
    query_type = Column(String(20), nullable=True)  # current | forecast | historical
    response_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SavedLocation(Base):
    """Locations a user has queried before — lets you skip geocoding on repeat asks."""
    __tablename__ = "saved_locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False, unique=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    country = Column(String(60), default="India")
    last_queried_at = Column(DateTime, default=datetime.utcnow)
