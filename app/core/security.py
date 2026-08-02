from dataclasses import dataclass
from fastapi import Header, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
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

development_api_key = APIKeyHeader(
    name="X-API-Key",
    scheme_name="DevelopmentApiKey",
    description="Development API key configured by the service operator.",
    auto_error=False,
)
bearer_auth = HTTPBearer(
    scheme_name="BearerAuth",
    description="The development API key supplied as a bearer token.",
    auto_error=False,
)

def require_scope(required_scope: str):
    async def dependency(
        x_api_key: str | None = Security(development_api_key),
        bearer: HTTPAuthorizationCredentials | None = Security(bearer_auth),
        x_client_id: str | None = Header(default="development-client"),
    ) -> Principal:
        settings = get_settings()
        supplied = bearer.credentials if bearer and bearer.scheme.lower() == "bearer" else x_api_key
        if supplied != settings.development_api_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing credentials")
        principal = Principal(subject="development-user", client_id=x_client_id or "development-client", scopes=ALL_DEV_SCOPES)
        if required_scope not in principal.scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient scope")
        return principal
    return dependency
