"""
Store each user's Monthly Wrap once their own month has ended. Safe to run repeatedly; run it daily from a scheduler:

  python -m app.scripts.generate_wraps                      # last month, for everyone whose month is over
  python -m app.scripts.generate_wraps --month 2026-09      # backfill one month
  python -m app.scripts.generate_wraps --user <uuid>        # one user only
"""

import argparse
from datetime import date
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

import app.db.base  # noqa: F401  (registers every model before the first query)
from app.models.monthly_wrap import MonthlyWrap
from app.models.user import User
from app.services.wrap import SCHEMA_VERSION, compute_metrics, current_month, month_bounds, persona, shift_month, user_timezone


def generate(db: Session, month: Optional[date] = None, user_id: Optional[UUID] = None) -> int:
    """Returns how many new snapshots were stored; existing ones are never touched."""
    users = db.query(User).options(selectinload(User.profile))
    if user_id:
        users = users.filter(User.id == user_id)
    stored = 0
    for user in users.all():
        tz = user_timezone(user)
        target = month or shift_month(current_month(tz), -1)
        start, end = month_bounds(target, tz)
        if target >= current_month(tz) or (user.created_at and end <= user.created_at):
            continue  # month not over locally yet, or the account did not exist
        metrics = compute_metrics(db, user.id, start, end)
        result = db.execute(insert(MonthlyWrap).values(
            id=uuid4(), user_id=user.id, month=target, timezone=tz, metrics=metrics,
            persona=persona(metrics), schema_version=SCHEMA_VERSION,
        ).on_conflict_do_nothing(constraint="uq_monthly_wraps_user_month"))
        stored += result.rowcount
    db.commit()
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--month", help="yyyy-mm (default: last month in each user's timezone)")
    parser.add_argument("--user", type=UUID, help="only this user id")
    args = parser.parse_args()
    month = date(int(args.month[:4]), int(args.month[5:7]), 1) if args.month else None

    from app.db.session import SessionLocal
    with SessionLocal() as db:
        print(f"Stored {generate(db, month, args.user)} new monthly wrap(s).")


if __name__ == "__main__":
    main()
