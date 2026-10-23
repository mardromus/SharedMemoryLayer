"""Keep the administrator account in step with the ADMIN_PASSWORD environment variable.

Render's free plan has no shell, so the environment is the only place to manage the
admin login. On every start this script creates the admin if it is missing, sets its
password to ADMIN_PASSWORD (so changing the variable and redeploying changes the
password) and clears any login lockout.
"""
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import session_scope
from app.models.identity import User
from app.security.passwords import hash_password, verify_password
from app.services.bootstrap import ensure_admin, ensure_rbac


def main() -> None:
    s = get_settings()
    if not s.admin_password:
        print("ADMIN_PASSWORD not set - admin account left unchanged")
        return
    with session_scope() as db:
        ensure_rbac(db)
        ensure_admin(db)
        user = db.scalar(select(User).where(User.username == s.admin_username))
        changed = not verify_password(s.admin_password, user.password_hash)
        if changed:
            user.password_hash = hash_password(s.admin_password)
        user.failed_login_count = 0
        user.locked_until = None
        user.is_active = True
    print(f"admin '{s.admin_username}' ready" + (" (password updated from ADMIN_PASSWORD)" if changed else ""))


if __name__ == "__main__":
    main()
