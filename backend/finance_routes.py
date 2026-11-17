"""REST API used by the MCP server. Every route needs a valid OAuth access token."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from . import services
from .database import get_db
from .deps import current_token, current_user
from .models import OAuthToken, User
from .schemas import BudgetIn, ExpenseIn

router = APIRouter(prefix="/api", tags=["finance"])


@router.get("/me")
def me(token: OAuthToken = Depends(current_token), user: User = Depends(current_user)):
    """The MCP server calls this to check a token is valid."""
    return {"user_id": user.id, "email": user.email, "client_id": token.client_id,
            "scopes": token.scope.split(), "expires_at": token.expires_at}


@router.post("/expenses", status_code=201)
def add_expense(body: ExpenseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return services.create_expense(db, user.id, body.amount, body.category, body.note, body.spent_on)


@router.get("/expenses")
def list_expenses(month: str | None = None, limit: int = 20,
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    return services.list_expenses(db, user.id, month, max(1, min(limit, 100)))


@router.delete("/expenses/{expense_id}")
def delete_expense(expense_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not services.delete_expense(db, user.id, expense_id):
        raise HTTPException(404, "Expense not found")
    return {"deleted": expense_id}


@router.get("/summary")
def summary(month: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return services.summary(db, user.id, month)


@router.put("/budgets")
def set_budget(body: BudgetIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return services.set_budget(db, user.id, body.category, body.monthly_limit)


@router.get("/budgets/status")
def budget_status(month: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return services.budget_status(db, user.id, month)
