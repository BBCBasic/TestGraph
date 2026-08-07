import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import get_settings


@dataclass
class Principal:
    subject: str
    client_id: str
    scopes: set[str]
    user_id: uuid.UUID | None = None


ALL_DEV_SCOPES = {
    "profile:read", "profile:write", "experience:read", "experience:draft",
    "experience:edit", "experience:publish", "experience:delete",
    "recommendation:read", "subject:write", "alignment:write",
    "reviews:read", "reviews:write",
}

PUBLIC_SCOPE_EQUIVALENTS = {
    "profile:read": "reviews:read",
    "profile:write": "reviews:write",
    "experience:read": "reviews:read",
    "experience:draft": "reviews:write",
    "experience:edit": "reviews:write",
    "experience:publish": "reviews:write",
    "experience:delete": "reviews:write",
    "recommendation:read": "reviews:read",
    "subject:write": "reviews:write",
    "alignment:write": "reviews:write",
}


class TokenError(ValueError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_access_token(*, user_id: uuid.UUID, client_id: str, scope: str, resource: str) -> tuple[str, int]:
    settings = get_settings()
    now = int(time.time())
    expires_in = settings.oauth_access_token_minutes * 60
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": settings.public_base_url.rstrip("/"), "aud": resource,
        "sub": f"tastegraph-user:{user_id}", "uid": str(user_id),
        "client_id": client_id, "scope": scope, "iat": now,
        "nbf": now - 5, "exp": now + expires_in, "jti": uuid.uuid4().hex,
    }
    encoded = f"{_b64url(json.dumps(header,separators=(',',':')).encode())}.{_b64url(json.dumps(payload,separators=(',',':')).encode())}"
    signature = hmac.new(settings.app_secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64url(signature)}", expires_in


def decode_access_token(token: str, expected_resource: str | None = None) -> dict:
    settings = get_settings()
    try:
        header_part, payload_part, signature_part = token.split(".")
        encoded = f"{header_part}.{payload_part}"
        expected = hmac.new(settings.app_secret.encode(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(signature_part)):
            raise TokenError("Invalid token signature")
        header = json.loads(_b64url_decode(header_part))
        payload = json.loads(_b64url_decode(payload_part))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        if isinstance(exc, TokenError):
            raise
        raise TokenError("Malformed access token") from exc
    now = int(time.time())
    resource = expected_resource or f"{settings.public_base_url.rstrip('/')}/mcp"
    if header.get("alg") != "HS256": raise TokenError("Unsupported token algorithm")
    if payload.get("iss") != settings.public_base_url.rstrip("/"): raise TokenError("Invalid token issuer")
    if payload.get("aud") != resource: raise TokenError("Invalid token audience")
    if int(payload.get("exp", 0)) <= now: raise TokenError("Access token expired")
    if int(payload.get("nbf", 0)) > now: raise TokenError("Access token is not active")
    return payload


def principal_from_authorization(authorization: str | None, required_scope: str, expected_resource: str | None = None) -> Principal:
    settings = get_settings()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise TokenError("No access token provided")
    token = authorization.split(" ", 1)[1]
    if hmac.compare_digest(token, settings.development_api_key):
        return Principal("development-user", "development-client", ALL_DEV_SCOPES)
    payload = decode_access_token(token, expected_resource=expected_resource)
    scopes = set(str(payload.get("scope", "")).split())
    equivalent = PUBLIC_SCOPE_EQUIVALENTS.get(required_scope)
    if required_scope not in scopes and (not equivalent or equivalent not in scopes):
        raise TokenError(f"Scope {required_scope} is required")
    try:
        user_id = uuid.UUID(payload["uid"])
    except (KeyError, ValueError) as exc:
        raise TokenError("Token has no valid TasteGraph user") from exc
    return Principal(payload["sub"], payload.get("client_id", "oauth-client"), scopes, user_id)

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
