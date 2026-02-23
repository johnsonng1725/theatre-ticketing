import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    # Local development fallback — SQLite, no PostgreSQL install required
    _db_path = os.path.join(os.path.dirname(__file__), "local_dev.db")
    DATABASE_URL    = f"sqlite:///{_db_path}"
    _connect_args   = {"check_same_thread": False}
else:
    # Render provides postgres:// but SQLAlchemy requires postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    _connect_args = {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
