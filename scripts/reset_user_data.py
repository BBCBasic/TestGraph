from __future__ import annotations
import argparse,json,uuid
from sqlalchemy import delete
from app.db.session import SessionLocal
from app.models.entities import AuditEvent,Experience,IdempotencyRecord,PairwiseAlignment,ProfileSignal,Subject
from app.models.deliberation import Deliberation, DeliberationContribution
from app.models.v2 import Assessment,LocationAssertion,FieldAlias,FieldDefinition,Source,SubjectRelationship,SubjectType,SubjectTypeAlias,SubjectTypeField,TypeRelationship,V2Experience,V2Subject

CONFIRMATION="RESET-ALL-USER-DATA-2026-08-08"
CONTENT_MODELS=(DeliberationContribution,Deliberation,LocationAssertion,Assessment,V2Experience,FieldAlias,SubjectTypeField,TypeRelationship,SubjectRelationship,V2Subject,Source,SubjectTypeAlias,FieldDefinition,SubjectType,ProfileSignal,PairwiseAlignment,Experience,Subject,IdempotencyRecord,AuditEvent)

def reset_user_data():
    with SessionLocal() as db:
        counts={};reset_id=f"user-data-reset-{uuid.uuid4()}"
        try:
            for model in CONTENT_MODELS:
                result=db.execute(delete(model));counts[model.__tablename__]=result.rowcount or 0
            db.add(AuditEvent(actor_id="system",client_id="maintenance",action="user_data_reset",object_type="database",object_id=reset_id,request_id=reset_id,details={"preserved":["users","schema_definitions","oauth_clients","oauth_authorization_codes","oauth_refresh_tokens","capability_credentials"],"deleted_rows":counts}))
            db.commit();return counts
        except Exception: db.rollback();raise

def main():
    parser=argparse.ArgumentParser(description="Delete TasteGraph review and vocabulary data.");parser.add_argument("--confirm",required=True);args=parser.parse_args()
    if args.confirm!=CONFIRMATION:parser.error(f"--confirm must exactly equal {CONFIRMATION}")
    print(json.dumps(reset_user_data(),sort_keys=True))

if __name__=="__main__":main()
