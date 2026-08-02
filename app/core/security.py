from dataclasses import dataclass
from fastapi import Header, HTTPException, status
from app.core.config import get_settings

@dataclass
class Principal:
    subject: str
    client_id: str
    scopes: set[str]

ALL_DEV_SCOPES = {
    "profile:read", "profile:write", "experience:read", "experience:draft",
    "experience:publish", "experience:delete", "recommendation:read", "subject:write"
}

def require_scope(required_scope: str):
    async def dependency(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
        x_client_id: str | None = Header(default="development-client"),
    ) -> Principal:
        settings = get_settings()
        supplied = x_api_key
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization.split(" ", 1)[1]
        if supplied != settings.development_api_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing credentials")
        principal = Principal(subject="development-user", client_id=x_client_id or "development-client", scopes=ALL_DEV_SCOPES)
        if required_scope not in principal.scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient scope")
        return principal
    return dependency
