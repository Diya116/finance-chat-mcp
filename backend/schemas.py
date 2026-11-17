"""Pydantic models: validate request bodies. FastAPI also uses them for /docs."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class ExpenseIn(BaseModel):
    amount: Decimal = Field(gt=0, le=10_000_000)
    category: str = Field(min_length=1, max_length=50)
    note: str = Field(default="", max_length=255)
    spent_on: date | None = None  # defaults to today


class BudgetIn(BaseModel):
    category: str = Field(min_length=1, max_length=50)
    monthly_limit: Decimal = Field(gt=0, le=100_000_000)
