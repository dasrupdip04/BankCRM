from functools import lru_cache
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt import PyJWKClient, decode
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.db import get_db
from app.models import ApplicationUser, Role, AuditLog
from app.settings import settings

bearer = HTTPBearer(auto_error=False)

@lru_cache
def jwks_client(url: str): return PyJWKClient(url, cache_keys=True)

def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)):
    if not credentials or not settings.supabase_url:
        raise HTTPException(401, "Authentication required")
    try:
        url = settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
        key = jwks_client(url).get_signing_key_from_jwt(credentials.credentials).key
        claims = decode(credentials.credentials, key, algorithms=["ES256", "RS256"], audience=settings.supabase_jwt_audience, issuer=settings.supabase_url.rstrip("/") + "/auth/v1")
        subject = claims.get("sub")
        if not subject: raise ValueError("Missing subject")
    except Exception:
        raise HTTPException(401, "Invalid or expired access token") from None
    user = db.query(ApplicationUser).filter_by(supabase_sub=subject).first()
    if not user:
        user = ApplicationUser(supabase_sub=subject, email=claims.get("email"))
        try:
            db.add(user); db.flush()
            customer_role = db.query(Role).filter_by(name="customer").first()
            if not customer_role:
                customer_role = Role(name="customer"); db.add(customer_role); db.flush()
            user.roles = [customer_role]
            db.commit(); db.refresh(user)
        except IntegrityError:
            # Concurrent first sign-ins may race on the unique Supabase subject.
            db.rollback()
            user = db.query(ApplicationUser).filter_by(supabase_sub=subject).first()
            if user is None: raise HTTPException(503,"User synchronization failed; retry sign-in") from None
    elif not user.roles:
        # Legacy synced users receive only the least-privileged default role.
        customer_role = db.query(Role).filter_by(name="customer").first()
        if not customer_role:
            customer_role = Role(name="customer"); db.add(customer_role); db.flush()
        user.roles = [customer_role]
        db.commit(); db.refresh(user)
    if claims.get("email") and user.email != claims["email"]:
        user.email = claims["email"]
        db.commit()
    return user

def require_roles(*names: str):
    def check(user: ApplicationUser = Depends(current_user)):
        if not {r.name for r in user.roles}.intersection(names): raise HTTPException(403, "Insufficient permissions")
        return user
    return check
