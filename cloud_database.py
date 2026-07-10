import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "")

if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.strip().strip('"').strip("'")

# Safe debugging print (masks password)
try:
    if DATABASE_URL:
        if "@" in DATABASE_URL and "://" in DATABASE_URL:
            scheme, rest = DATABASE_URL.split("://", 1)
            if "@" in rest:
                creds, host = rest.split("@", 1)
                if ":" in creds:
                    user, passwd = creds.split(":", 1)
                    masked = f"{user}:***"
                else:
                    masked = "***"
                print(f"LOG: Loaded DATABASE_URL scheme={scheme}, host={host}")
            else:
                print(f"LOG: Loaded DATABASE_URL (no @ symbol): {DATABASE_URL[:30]}...")
        else:
            print(f"LOG: Loaded DATABASE_URL (invalid format): {DATABASE_URL[:30]}... (length: {len(DATABASE_URL)})")
    else:
        print("LOG: DATABASE_URL is empty, will fallback to SQLite.")
except Exception as e:
    print(f"LOG: Error printing database url: {e}")

if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./biotime_cloud.db"

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
