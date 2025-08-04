from sqlalchemy import Table, Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from .db import metadata

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("role", String(10), nullable=False),
    Column("message", Text, nullable=False),
    Column("timestamp", DateTime(timezone=True), server_default=func.now())
)
