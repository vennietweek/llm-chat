# app/db.py
import os
from databases import Database
from sqlalchemy import create_engine, MetaData

# Get database URL from environment variable
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://chatuser:secretpassword@localhost:5432/chatdb")

# Create async database connection
database = Database(DATABASE_URL)

# SQLAlchemy setup for creating tables
engine = create_engine(DATABASE_URL)
metadata = MetaData()