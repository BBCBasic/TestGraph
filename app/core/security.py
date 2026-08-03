import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import get_settings


@dataclass
class Principal:
    subject: str
    client_id: str
    scopes: set[str]


ALL_DEV_SCOPES = {
    "profile:read", "profile:write", "experience:read", "experience:draft",
    "experience:edit", "experience:publish", "experience:delete",
    "recommendation:read", "subject:write", "alignment:write",
}

api_key_header = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ApiKey",
    description="A server-issued, revocable client credential. Client identity and scopes are derived from the credential.",
    auto_error=False,
)


def _principal_for_key(supplied: str) -> Principal | None:
    settings = get_settings()
    if hmac.compare_digest(supplied, settings.development_api_key):
        return Principal(subject="development-user", client_id="development-client", scopes=ALL_DEV_SCOPES)
    for client_id, credential in settings.client_api_keys.items():
        if hmac.compare_digest(supplied, credential.secret):
            return Principal(subject=credential.subject, client_id=client_id, scopes=set(credential.scopes))
    return None


async def optional_principal(x_api_key: str | None = Security(api_key_header)) -> Principal | None:
    if x_api_key is None:
        return None
    principal = _principal_for_key(x_api_key)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return principal


def require_scope(required_scope: str):
    async def dependency(principal: Principal | None = Security(optional_principal)) -> Principal:
        if principal is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")
        if required_scope not in principal.scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient scope")
        return principal
    return dependency
