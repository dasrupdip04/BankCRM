from functools import lru_cache
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt import PyJWKClient, decode
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import ApplicationUser
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
        db.add(user); db.commit(); db.refresh(user)
    return user

def require_roles(*names: str):
    def check(user: ApplicationUser = Depends(current_user)):
        if not {r.name for r in user.roles}.intersection(names): raise HTTPException(403, "Insufficient permissions")
        return user
    return check
