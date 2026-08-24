from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

import config

# echo=False in prod; flip to True locally if you need to see generated SQL.
engine = create_engine(config.MIDDLEWARE_DB_URL, pool_pre_ping=True, echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a session, always closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()