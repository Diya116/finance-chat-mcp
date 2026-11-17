"""Business logic (all the SQL lives here).

Routes (API and web pages) call these functions, so logic is written once.
EVERY query filters by user_id: that is what keeps users' data separate.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Budget, Expense


class BadRequest(ValueError):
    """Raised for bad user input; main.py turns it into an HTTP 400."""


# ---- money / dates ----

def to_paise(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_paise(paise: int) -> float:
    return round(paise / 100, 2)


def month_range(month: str | None) -> tuple[str, date, date]:
    """'2026-11' -> ('2026-11', 2026-11-01, 2026-12-01). None means the current month."""
    try:
        if month:
            year, mon = (int(part) for part in month.split("-"))
        else:
            today = date.today()
            year, mon = today.year, today.month
        start = date(year, mon, 1)
    except ValueError:
        raise BadRequest("month must look like 2026-11")
    end = date(year + (mon == 12), mon % 12 + 1, 1)
    return start.strftime("%Y-%m"), start, end


# ---- expenses ----

def expense_dict(e: Expense) -> dict:
    return {"id": e.id, "amount": from_paise(e.amount_paise), "category": e.category,
            "note": e.note, "spent_on": e.spent_on.isoformat()}


def create_expense(db: Session, user_id: int, amount: Decimal, category: str,
                   note: str = "", spent_on: date | None = None) -> dict:
    expense = Expense(user_id=user_id, amount_paise=to_paise(amount),
                      category=category.strip().lower(), note=note.strip(),
                      spent_on=spent_on or date.today())
    db.add(expense)
    db.commit()
    return expense_dict(expense)


def list_expenses(db: Session, user_id: int, month: str | None = None, limit: int = 20) -> list[dict]:
    _, start, end = month_range(month)
    rows = db.scalars(
        select(Expense)
        .where(Expense.user_id == user_id, Expense.spent_on >= start, Expense.spent_on < end)
        .order_by(Expense.spent_on.desc(), Expense.id.desc())
        .limit(limit)
    )
    return [expense_dict(e) for e in rows]


def delete_expense(db: Session, user_id: int, expense_id: int) -> bool:
    expense = db.scalar(select(Expense).where(Expense.id == expense_id, Expense.user_id == user_id))
    if expense is None:
        return False
    db.delete(expense)
    db.commit()
    return True


# ---- summary / budgets ----

def _spent_by_category(db: Session, user_id: int, start: date, end: date) -> list[tuple[str, int, int]]:
    total = func.sum(Expense.amount_paise)
    return db.execute(
        select(Expense.category, total, func.count())
        .where(Expense.user_id == user_id, Expense.spent_on >= start, Expense.spent_on < end)
        .group_by(Expense.category)
        .order_by(total.desc())
    ).all()


def summary(db: Session, user_id: int, month: str | None = None) -> dict:
    label, start, end = month_range(month)
    rows = _spent_by_category(db, user_id, start, end)
    return {
        "month": label,
        "total": from_paise(sum(r[1] for r in rows)),
        "by_category": [{"category": c, "total": from_paise(t), "count": n} for c, t, n in rows],
    }


def set_budget(db: Session, user_id: int, category: str, monthly_limit: Decimal) -> dict:
    category = category.strip().lower()
    budget = db.scalar(select(Budget).where(Budget.user_id == user_id, Budget.category == category))
    if budget is None:
        budget = Budget(user_id=user_id, category=category, monthly_limit_paise=0)
        db.add(budget)
    budget.monthly_limit_paise = to_paise(monthly_limit)
    db.commit()
    return {"category": category, "monthly_limit": from_paise(budget.monthly_limit_paise)}


def budget_status(db: Session, user_id: int, month: str | None = None) -> list[dict]:
    _, start, end = month_range(month)
    spent = {c: t for c, t, _ in _spent_by_category(db, user_id, start, end)}
    budgets = db.scalars(select(Budget).where(Budget.user_id == user_id).order_by(Budget.category))
    result = []
    for b in budgets:
        used = spent.get(b.category, 0)
        result.append({
            "category": b.category,
            "monthly_limit": from_paise(b.monthly_limit_paise),
            "spent": from_paise(used),
            "remaining": from_paise(b.monthly_limit_paise - used),
            "over_budget": used > b.monthly_limit_paise,
        })
    return result
