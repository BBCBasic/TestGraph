from __future__ import annotations

import argparse
import json
import uuid

from sqlalchemy import delete

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
    ConceptFieldProposal,
    FieldAlias,
    Source,
    V2Experience,
    V2Subject,
)


CONFIRMATION = "RESET-ALL-USER-DATA-2026-08-08"

# Child tables must be cleared before the parent tables they reference.
CONTENT_MODELS = (
    Assessment,
    V2Experience,
    SemanticAliasProposal,
    FieldAlias,
    ConceptFieldProposal,
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
        counts: dict[str, int] = {}
        reset_id = f"user-data-reset-{uuid.uuid4()}"
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
                    object_id=reset_id,
                    request_id=reset_id,
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
