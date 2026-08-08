from __future__ import annotations

import argparse
import json

from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.models.entities import (
    AuditEvent,
    Experience,
    IdempotencyRecord,
    PairwiseAlignment,
    ProfileSignal,
    Subject,
)
from app.models.semantic import SemanticAliasProposal
from app.models.v2 import (
    Assessment,
    Concept,
    ConceptField,
    FieldAlias,
    Source,
    V2Experience,
    V2Subject,
)


CONFIRMATION = "RESET-ALL-USER-DATA-2026-08-08"
MARKER = "user-data-reset-2026-08-08-v1"

# Child tables must be cleared before the parent tables they reference.
CONTENT_MODELS = (
    Assessment,
    V2Experience,
    SemanticAliasProposal,
    FieldAlias,
    ConceptField,
    V2Subject,
    Source,
    Concept,
    ProfileSignal,
    PairwiseAlignment,
    Experience,
    Subject,
    IdempotencyRecord,
    AuditEvent,
)


def reset_user_data() -> dict[str, int]:
    """Remove review/knowledge data while preserving accounts and authentication."""
    with SessionLocal() as db:
        already_ran = db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.action == "user_data_reset",
                AuditEvent.object_id == MARKER,
            )
        )
        if already_ran:
            return {"already_reset": 1}

        counts: dict[str, int] = {}
        try:
            for model in CONTENT_MODELS:
                result = db.execute(delete(model))
                counts[model.__tablename__] = result.rowcount or 0

            db.add(
                AuditEvent(
                    actor_id="system",
                    client_id="maintenance",
                    action="user_data_reset",
                    object_type="database",
                    object_id=MARKER,
                    request_id=MARKER,
                    details={
                        "preserved": [
                            "users",
                            "schema_definitions",
                            "oauth_clients",
                            "oauth_authorization_codes",
                            "oauth_refresh_tokens",
                            "capability_credentials",
                        ],
                        "deleted_rows": counts,
                    },
                )
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete TasteGraph v1/v2 review and knowledge data."
    )
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        parser.error(f"--confirm must exactly equal {CONFIRMATION}")

    print(json.dumps(reset_user_data(), sort_keys=True))


if __name__ == "__main__":
    main()
