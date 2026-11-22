# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authentication API: local sign-in, self-registration, sign-out, user approval and JWTs.

Issues and validates the HS256 session tokens (SECRET_KEY) used by every UI route, provides
the get_current_user / require_admin dependencies, and audits every sign-in attempt.
Alternative password sign-ins (Enterprise LDAP/AD) come from the plugin registry.
"""
from fastapi import APIRouter, HTTPException, Depends, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from datetime import datetime, timezone, timedelta
from typing import Optional
import bcrypt
import jwt
import os
import uuid

from axara.community.database import get_db
from axara.community.database.models import User
from starlette.concurrency import run_in_threadpool
from axara.community.common.audit import record as audit_record, client_ip

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SECRET_KEY         = os.getenv("SECRET_KEY", "change_this_secret_key_in_production")
# Fail fast on the built-in default so a misconfigured deployment can't ship with
# a publicly-known JWT signing key (forgeable admin tokens). Set SECRET_KEY in the
# environment; set ALLOW_INSECURE_SECRET_KEY=1 only for local development.
if SECRET_KEY == "change_this_secret_key_in_production" and \
        os.getenv("ALLOW_INSECURE_SECRET_KEY", "").strip().lower() not in ("1", "true", "yes"):
    raise RuntimeError(
        "SECRET_KEY is the built-in default. Set a strong SECRET_KEY in the "
        "environment before starting (or ALLOW_INSECURE_SECRET_KEY=1 for dev)."
    )
ALGORITHM          = "HS256"
TOKEN_EXPIRY_HOURS = 0.5  # 30 minutes

VALID_ROLES = {"admin", "operator", "viewer"}

# Constant-time login for unknown usernames: without this, a missing user returns
# before any bcrypt work, so response timing reveals which usernames exist.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt()).decode()

# Declare HTTPBearer so FastAPI registers it as a proper OpenAPI security
# scheme. This makes the Swagger UI Authorize button prompt for a Bearer
# token and send it automatically with every protected request.
http_bearer = HTTPBearer(auto_error=False)


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    """Username and password sign-in form."""
    username: str
    password: str


class LoginResponse(BaseModel):
    """Session token and the user's profile."""
    token:    str
    username: str
    email:    Optional[str]
    role:     Optional[str]


class RegisterRequest(BaseModel):
    """Self-registration form."""
    username: str
    email:    str
    password: str
    role:     str = "viewer"


class RegisterResponse(BaseModel):
    """Result of a self-registration (account pending approval)."""
    message:  str
    username: str


class LogoutRequest(BaseModel):
    """Legacy sign-out body (ignored; sign-out acts on the caller's token)."""
    username: str


# ── Helpers ──────────────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    """Check a password against a bcrypt hash.

    Args:
        plain: Password typed by the user.
        hashed: Stored bcrypt hash.

    Returns:
        True when they match.
    """
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user: User) -> str:
    """Issue a session JWT for a user.

    Args:
        user: User row.

    Returns:
        The signed token (expires after TOKEN_EXPIRY_HOURS).

    Raises:
        HTTPException: 403 when a plugin's login check refuses the user (e.g. Enterprise
            licence blocked: administrators only).
    """
    from axara.community.plugins.loader import get_registry
    get_registry().run_checks("login", user)
    now = datetime.now(timezone.utc)
    payload = {
        "sub":      user.username,
        "role":     user.role,
        "user_id":  str(user.id),
        "iat":      now,                                        # issued-at
        "exp":      now + timedelta(hours=TOKEN_EXPIRY_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Verify and decode a session JWT.

    Args:
        token: Bearer token.

    Returns:
        The token payload.

    Raises:
        jwt.ExpiredSignatureError: when the token has expired.
        jwt.PyJWTError: when the token is invalid.
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
    db: Session = Depends(get_db)
) -> User:
    """Dependency — validates JWT and returns the User row."""
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.username == payload["sub"]).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # Reject accounts that are no longer permitted to use the system.
    # SECURITY (added with user management): login() already blocks disabled and
    # pending accounts, but login is only the FRONT DOOR — a token issued while
    # the account was healthy stays valid for its full TOKEN_EXPIRY_HOURS
    # lifetime. Without this check, deactivating a user had no effect on their
    # already-open session: every protected endpoint kept accepting their
    # existing JWT for up to 30 more minutes. The deactivate endpoint also
    # stamps token_invalidated_at (belt-and-braces, and the mechanism that
    # covers any other status transition), but this check is what makes
    # deactivation authoritative on its own rather than best-effort.
    if user.status == "disabled":
        raise HTTPException(status_code=401, detail="Account disabled. Contact your administrator.")
    if not user.is_approved or user.status == "pending":
        raise HTTPException(status_code=401, detail="Account pending admin approval.")

    # Reject tokens issued before the last logout (server-side invalidation)
    if user.token_invalidated_at:
        iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
        if iat <= user.token_invalidated_at:
            raise HTTPException(status_code=401, detail="Session expired, please log in again")

    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency: the signed-in user, who must be an administrator.

    Args:
        current_user: From get_current_user.

    Returns:
        The User.

    Raises:
        HTTPException: 403 for non-administrators.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(request: RegisterRequest, http_request: Request, db: Session = Depends(get_db)):
    """
    Register a new user account.
    The account is created with status='pending' and is_approved=False.
    The user cannot log in until an admin approves via POST /auth/approve/{username}.
    """
    from axara.community.plugins.loader import get_registry
    if not get_registry().local_registration_enabled():
        raise HTTPException(status_code=403,
                            detail="Self-registration is disabled. Sign in with your directory account.")
    if len(request.password) < 12 or request.password.lower() == request.username.lower():
        raise HTTPException(status_code=400,
                            detail="Password must be at least 12 characters and must not equal the username.")
    if request.role not in VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role. Must be one of: {', '.join(sorted(VALID_ROLES))}"
        )

    if db.query(User).filter(User.username == request.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    if db.query(User).filter(User.email == request.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    password_hash = bcrypt.hashpw(
        request.password.encode(), bcrypt.gensalt()
    ).decode()

    user = User(
        id            = str(uuid.uuid4()),
        username      = request.username,
        email         = request.email,
        password_hash = password_hash,
        role          = request.role,
        is_approved   = False,
        status        = "pending",
        created_at    = datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()

    await run_in_threadpool(
        audit_record, "auth.register", actor=user.username, actor_id=str(user.id),
        resource_type="user", resource_id=user.username,
        source_ip=client_ip(http_request), user_agent=http_request.headers.get("user-agent"),
        details={"requested_role": request.role, "email": request.email},
    )

    return RegisterResponse(
        message  = "Registration submitted. Your account is pending admin approval.",
        username = user.username,
    )


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, http_request: Request, db: Session = Depends(get_db)):
    """
    Authenticate a user (local accounts).
    Rejects accounts that are pending approval or disabled.
    Every attempt — success or failure, with the reason — is written to the audit log.
    """
    ip, ua = client_ip(http_request), http_request.headers.get("user-agent")

    async def _audit(outcome, reason=None, user_obj=None):
        """Write the sign-in attempt to the audit log.

        Args:
            outcome: 'success', 'failure' or 'denied'.
            reason: Why it failed, if it did.
            user_obj: The User row, when known.
        """
        await run_in_threadpool(
            audit_record, "auth.login", actor=request.username,
            actor_id=str(user_obj.id) if user_obj else None,
            resource_type="user", resource_id=request.username, outcome=outcome,
            source_ip=ip, user_agent=ua,
            details={"method": "local", **({"reason": reason} if reason else {})},
        )

    user = db.query(User).filter(User.username == request.username).first()

    # Alternative password sign-ins registered by plugins (e.g. Enterprise LDAP/AD). Each
    # returns a LoginResponse, None to fall through to local sign-in, or raises HTTPException.
    from axara.community.plugins.loader import get_registry
    for hook in get_registry().password_logins():
        result = await hook(request, db, user, ip, ua)
        if result is not None:
            return result

    # Same generic message for missing user and wrong password (no enumeration),
    # and the same bcrypt cost either way (no timing oracle).
    if not user or not user.password_hash:
        verify_password(request.password, _DUMMY_HASH)
        await _audit("failure", "unknown_user" if not user else "no_local_password", user)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not verify_password(request.password, user.password_hash):
        await _audit("failure", "bad_password", user)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.is_approved or user.status == "pending":
        await _audit("denied", "pending_approval", user)
        raise HTTPException(
            status_code=403,
            detail="Account pending admin approval. You will be notified when access is granted."
        )

    if user.status == "disabled":
        await _audit("denied", "disabled", user)
        raise HTTPException(status_code=403, detail="Account disabled. Contact your administrator.")

    user.last_login = datetime.now(timezone.utc)
    db.commit()
    await _audit("success", user_obj=user)

    return LoginResponse(
        token    = create_token(user),
        username = user.username,
        email    = user.email,
        role     = user.role,
    )


@router.post("/logout")
async def logout(
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Invalidate the CALLER's session (token_invalidated_at); any JWT issued at or
    before this time is rejected by get_current_user().

    SECURITY FIX: this previously took a username in the request body with no
    authentication, so anyone could force-log-out any user (including admins).
    It now acts only on the authenticated caller; any body is ignored.
    """
    current_user.token_invalidated_at = datetime.now(timezone.utc)
    db.commit()
    await run_in_threadpool(
        audit_record, "auth.logout", actor=current_user.username,
        actor_id=str(current_user.id), resource_type="user",
        resource_id=current_user.username, source_ip=client_ip(http_request),
        user_agent=http_request.headers.get("user-agent"),
    )
    return {"message": "Logged out successfully"}


@router.post("/approve/{username}")
async def approve_user(
    username: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Admin endpoint — approve a pending registration.
    Sets is_approved=True and status='active'.
    """
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_approved:
        return {"message": f"{username} is already approved"}
    user.is_approved = True
    user.status      = "active"
    db.commit()
    return {"message": f"User '{username}' approved successfully"}


@router.get("/pending-users")
async def list_pending_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Admin endpoint — list all accounts awaiting approval."""
    users = db.query(User).filter(User.status == "pending").all()
    return [
        {
            "username":   u.username,
            "email":      u.email,
            "role":       u.role,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.get("/users")
async def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Admin endpoint — list ALL user accounts with their current status.

    Distinct from /pending-users (which returns only status='pending' for the
    approval queue): this is the full roster used by the user-management view,
    including active and disabled accounts.
    """
    users = db.query(User).order_by(User.username).all()
    return [
        {
            "id":          u.id,
            "username":    u.username,
            "email":       u.email,
            "role":        u.role,
            "status":      u.status,
            "auth_source": getattr(u, "auth_source", "local"),
            "is_approved": u.is_approved,
            "created_at":  u.created_at.isoformat() if u.created_at else None,
            "last_login":  u.last_login.isoformat() if u.last_login else None,
        }
        for u in users
    ]


def _active_admin_count(db: Session, exclude_username: str = None) -> int:
    """How many admins would still be able to log in, optionally ignoring one."""
    q = db.query(User).filter(
        User.role == "admin",
        User.status == "active",
        User.is_approved.is_(True),
    )
    if exclude_username:
        q = q.filter(User.username != exclude_username)
    return q.count()


@router.post("/users/{username}/deactivate")
async def deactivate_user(
    username: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Admin endpoint — disable an account and immediately terminate its session.

    Two lockout guardrails, enforced HERE rather than only in the UI (hiding a
    button does not stop a direct API call):
      1. An admin cannot deactivate their own account.
      2. The last remaining active admin cannot be deactivated — otherwise
         nobody could ever administer the system again, with no recovery path
         short of direct database access.

    Sets token_invalidated_at as well as status, so any JWT already issued to
    this user is rejected on its next request instead of remaining valid for
    the rest of its lifetime.
    """
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.username == admin.username:
        raise HTTPException(
            status_code=400,
            detail="You cannot deactivate your own account.",
        )

    if user.role == "admin" and _active_admin_count(db, exclude_username=username) == 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot deactivate the last active admin — at least one must remain.",
        )

    if user.status == "disabled":
        return {"message": f"{username} is already disabled", "status": "disabled"}

    user.status = "disabled"
    # Kill any in-flight session immediately (see get_current_user).
    user.token_invalidated_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": f"User '{username}' deactivated", "status": "disabled"}


@router.post("/users/{username}/reactivate")
async def reactivate_user(
    username: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Admin endpoint — restore a disabled account to active.

    Only valid for accounts that were previously approved and then disabled.
    A 'pending' account must go through /approve/{username} instead — that is
    the approval decision, not a reactivation, and conflating the two would let
    an admin skip the approval queue by mistake.
    """
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.status == "pending" or not user.is_approved:
        raise HTTPException(
            status_code=400,
            detail="Account is pending approval — use approve instead of reactivate.",
        )

    if user.status == "active":
        return {"message": f"{username} is already active", "status": "active"}

    user.status = "active"
    db.commit()
    return {"message": f"User '{username}' reactivated", "status": "active"}


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return {
        "username": current_user.username,
        "email":    current_user.email,
        "role":     current_user.role,
        "status":   current_user.status,
    }