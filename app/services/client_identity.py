from __future__ import annotations


def canonical_client_identity(source_client: str | None) -> str | None:
    """Preserve the authenticated client ID exactly as issued by the principal."""
    return source_client
