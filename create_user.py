"""
Manually create an internal logistics interface account. This is THE ONLY
way an account ever gets created — there is no signup endpoint anywhere in
main.py, intentionally. Run this directly on VM101 whenever a new team
member needs access.

Usage:
    docker-compose run --rm atome-middleware python3 create_user.py
"""

import getpass

from database import SessionLocal
from db_models import User
from auth import hash_password

if __name__ == "__main__":
    email = input("Email: ").strip().lower()
    if not email or "@" not in email:
        raise SystemExit("Invalid email.")

    password = getpass.getpass("Password: ")
    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        raise SystemExit("Passwords don't match.")
    if len(password) < 12:
        raise SystemExit("Password must be at least 12 characters.")

    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(email=email).first()
        if existing:
            raise SystemExit(f"A user with email {email} already exists.")

        user = User(email=email, password_hash=hash_password(password))
        db.add(user)
        db.commit()
        print(f"Created user: {email}")
    finally:
        db.close()
