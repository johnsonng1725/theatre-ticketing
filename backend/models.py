from sqlalchemy import Column, String, Boolean, DateTime, Integer, Text
from sqlalchemy.sql import func
from database import Base


# ── Event settings (key-value store) ────────────────────────────────────────
class Setting(Base):
    """
    Stores all configurable event settings as key-value pairs.
    Default values are defined in main.py; only overrides live here.
    """
    __tablename__ = "settings"
    key   = Column(String(60),  primary_key=True)
    value = Column(Text,        nullable=False)


class Ticket(Base):
    __tablename__ = "tickets"

    ticket_id        = Column(String(32),  primary_key=True, index=True)
    name             = Column(String(100), nullable=False)
    email            = Column(String(255), nullable=False, index=True)
    phone            = Column(String(20),  nullable=False)
    ticket_type      = Column(String(50),  nullable=False)
    show_date        = Column(String(20),  nullable=False)
    quantity         = Column(Integer,     default=1, nullable=False)
    # Receipt stored as base64 data URI — sufficient for small-scale events
    receipt_data     = Column(Text,        nullable=True)
    receipt_filename = Column(String(255), nullable=True)
    # 'pending' | 'receipt_uploaded'
    payment_status   = Column(String(20),  default='pending', nullable=False)
    checked_in       = Column(Boolean,     default=False, nullable=False)
    checked_in_at    = Column(DateTime(timezone=True), nullable=True)
    created_at       = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
