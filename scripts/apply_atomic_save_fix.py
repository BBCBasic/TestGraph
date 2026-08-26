from pathlib import Path


service_path = Path("app/services/v2.py")
service = service_path.read_text()
start = service.index("def create_experience(")
end = service.index("\ndef delete_owned_experience", start)
segment = service[start:end]
old_signature = "def create_experience(db: Session, payload: ExperienceCreate, client_id: str) -> V2Experience:\n"
new_signature = (
    "def create_experience(\n"
    "    db: Session, payload: ExperienceCreate, client_id: str, *, commit: bool = True,\n"
    ") -> V2Experience:\n"
)
if segment.count(old_signature) != 1:
    raise RuntimeError("create_experience signature did not match expected source")
segment = segment.replace(old_signature, new_signature, 1)
old_tail = "    db.add(obj); db.commit(); db.refresh(obj)\n    return obj\n"
new_tail = (
    "    db.add(obj)\n"
    "    if commit:\n"
    "        db.commit(); db.refresh(obj)\n"
    "    else:\n"
    "        db.flush()\n"
    "    return obj\n"
)
if segment.count(old_tail) != 1:
    raise RuntimeError("create_experience commit tail did not match expected source")
segment = segment.replace(old_tail, new_tail, 1)
service = service[:start] + segment + service[end:]
service_path.write_text(service)


api_path = Path("app/api/mcp_v2.py")
api = api_path.read_text()
start = api.index("def _save_experience(")
end = api.index("\ndef _delete_experience", start)
segment = api[start:end]

old_order = '''    enrichment_check, check_error = _validate_subject_enrichment_check(\n        args.get("subject_enrichment_check"), args\n    )\n    if check_error is not None:\n        return check_error\n    collection_assessment, collection_error = _validate_collection_assessment(\n        args.get("collection_assessment"), args, enrichment_check, db=db\n    )\n    if collection_error is not None:\n        return collection_error\n    client_id = f"{principal.client_id}:v3"\n    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}\n    payload_hash, prior = begin_idempotent_write(db, client_id=client_id, key=f"experience:{args['idempotency_key']}", payload=relevant)\n    if prior is not None:\n        return _result(prior)\n'''
new_order = '''    client_id = f"{principal.client_id}:v3"\n    relevant = {k: v for k, v in args.items() if k != "idempotency_key"}\n    payload_hash, prior = begin_idempotent_write(\n        db, client_id=client_id, key=f"experience:{args['idempotency_key']}", payload=relevant\n    )\n    if prior is not None:\n        return _result(prior)\n    enrichment_check, check_error = _validate_subject_enrichment_check(\n        args.get("subject_enrichment_check"), args\n    )\n    if check_error is not None:\n        return check_error\n    collection_assessment, collection_error = _validate_collection_assessment(\n        args.get("collection_assessment"), args, enrichment_check, db=db\n    )\n    if collection_error is not None:\n        return collection_error\n'''
if segment.count(old_order) != 1:
    raise RuntimeError("save_experience idempotency ordering did not match expected source")
segment = segment.replace(old_order, new_order, 1)

old_subject = "        client_id,\n    )\n    context = ensure_subject_context("
new_subject = "        client_id, commit=False,\n    )\n    context = ensure_subject_context("
if segment.count(old_subject) != 1:
    raise RuntimeError("save_experience ensure_subject call did not match expected source")
segment = segment.replace(old_subject, new_subject, 1)
old_context = (
    "        db, subject, context_payload,\n"
    "        client_id=client_id, owner_id=principal.user_id,\n"
    "    )\n"
)
new_context = (
    "        db, subject, context_payload,\n"
    "        client_id=client_id, owner_id=principal.user_id, commit=False,\n"
    "    )\n"
)
if segment.count(old_context) != 1:
    raise RuntimeError("save_experience ensure_subject_context call did not match expected source")
segment = segment.replace(old_context, new_context, 1)
old_create = "        client_id,\n    )\n    body = {\n"
new_create = "        client_id, commit=False,\n    )\n    body = {\n"
if segment.count(old_create) != 1:
    raise RuntimeError("save_experience create_experience call did not match expected source")
segment = segment.replace(old_create, new_create, 1)
api = api[:start] + segment + api[end:]
api_path.write_text(api)


test_path = Path("tests/test_save_experience_atomic.py")
test_path.write_text('''import json\nimport uuid\n\nimport pytest\nfrom sqlalchemy import create_engine, func, select\nfrom sqlalchemy.orm import Session\n\nfrom app.api.mcp_v2 import _save_experience\nfrom app.core.security import Principal\nfrom app.db.base import Base\nfrom app.models.entities import IdempotencyRecord\nfrom app.models.v2 import SubjectRelationship, V2Experience, V2Subject\nfrom app.services.v2 import ensure_subject_type\n\n\nSOURCE = "https://example.test/rockwealth/locations"\n\n\n@pytest.fixture()\ndef db():\n    engine = create_engine("sqlite+pysqlite:///:memory:")\n    Base.metadata.create_all(engine)\n    with Session(engine) as session:\n        yield session\n\n\n@pytest.fixture()\ndef principal():\n    return Principal(\n        subject="test-user", client_id="test-client",\n        scopes={"reviews:write"}, user_id=uuid.uuid4(),\n    )\n\n\ndef _args(structured_data):\n    return {\n        "subject_type": "financial service",\n        "subject_name": "Rockwealth Cotswolds",\n        "canonical_key": "rockwealth-cotswolds-cirencester",\n        "identifiers": {"website": "https://example.test/rockwealth/cotswolds"},\n        "subject_attributes": {},\n        "subject_provenance": {},\n        "subject_enrichment_check": {\n            "status": "completed",\n            "sources": [SOURCE],\n            "applied_fields": {\n                SOURCE: [\n                    "subject_context.subjects[0].identifiers.directory_url",\n                    "subject_context.subjects[0].attributes.discovered_count",\n                    "subject_context.relationships[0]",\n                    "subject_context.relationships[1]",\n                ],\n            },\n            "retrieval_uses": {\n                "subject_context.subjects[0].identifiers.directory_url": {\n                    "roles": ["relationship"],\n                    "likely_queries": ["Rockwealth locations"],\n                    "reason": "Supports future collection discovery.",\n                },\n                "subject_context.subjects[0].attributes.discovered_count": {\n                    "roles": ["verification"],\n                    "reason": "Supports collection completeness verification.",\n                },\n                "subject_context.relationships[0]": {\n                    "roles": ["relationship"],\n                    "likely_queries": ["Rockwealth Cotswolds"],\n                    "reason": "Connects the reviewed branch to the collection.",\n                },\n                "subject_context.relationships[1]": {\n                    "roles": ["relationship"],\n                    "likely_queries": ["Rockwealth Cheltenham"],\n                    "reason": "Connects the sibling branch to the collection.",\n                },\n            },\n        },\n        "collection_assessment": {\n            "status": "member",\n            "collection_name": "Rockwealth",\n            "collection_type": "financial service",\n            "directory_url": SOURCE,\n            "discovered_count": 2,\n            "submitted_member_refs": ["reviewed_subject", "cheltenham"],\n            "source_manifest": {\n                "coverage_status": "complete",\n                "coverage_method": "single_page",\n                "declared_source_count": 1,\n                "source_pages": [{\n                    "url": SOURCE,\n                    "source_kind": "directory_page",\n                    "member_refs": ["reviewed_subject", "cheltenham"],\n                    "terminal": True,\n                }],\n                "discovery_queries": ["Rockwealth official locations"],\n                "exhaustion_evidence": "The authoritative directory is a finite single-page list of all locations.",\n                "unresolved_source_urls": [],\n            },\n            "evidence_sources": [SOURCE],\n        },\n        "headline": "Interesting first meeting",\n        "summary": "Initial free consultation; minded to take this further.",\n        "raw_text": (\n            "We had a chat with Rock Wealth in Cirencester. Our initial free consultation "\n            "with Nicola and Andy. Interesting first meeting. We are minded to take this further."\n        ),\n        "structured_data": structured_data,\n        "subject_context": {\n            "subjects": [\n                {\n                    "ref": "rockwealth",\n                    "subject_type": "financial service",\n                    "name": "Rockwealth",\n                    "canonical_key": "rockwealth",\n                    "identifiers": {"directory_url": SOURCE},\n                    "attributes": {"discovered_count": 2},\n                    "provenance": {"source": SOURCE},\n                },\n                {\n                    "ref": "cheltenham",\n                    "subject_type": "financial service",\n                    "name": "Rockwealth Cheltenham",\n                    "canonical_key": "rockwealth-cheltenham",\n                    "identifiers": {},\n                    "attributes": {},\n                    "provenance": {"source": SOURCE},\n                },\n            ],\n            "relationships": [\n                {\n                    "source_ref": "reviewed_subject",\n                    "relationship": "belongs_to",\n                    "target_ref": "rockwealth",\n                    "provenance": {"source": SOURCE},\n                },\n                {\n                    "source_ref": "cheltenham",\n                    "relationship": "belongs_to",\n                    "target_ref": "rockwealth",\n                    "provenance": {"source": SOURCE},\n                },\n            ],\n        },\n        "visibility": "private",\n        "user_approved": True,\n        "idempotency_key": "rockwealth-regression-atomic-save",\n    }\n\n\ndef _count(db, model):\n    return db.scalar(select(func.count()).select_from(model))\n\n\ndef test_failed_save_experience_rolls_back_graph_and_retry_is_idempotent(db, principal):\n    ensure_subject_type(db, "financial service", created_by="test")\n\n    with pytest.raises(ValueError, match="not registered globally"):\n        _save_experience(db, principal, _args({"consultation_type": "initial free consultation"}))\n\n    # Emulate request/session failure cleanup. Before the fix, subjects and relationships\n    # had already been committed and therefore survived this rollback.\n    db.rollback()\n    assert _count(db, V2Subject) == 0\n    assert _count(db, SubjectRelationship) == 0\n    assert _count(db, V2Experience) == 0\n    assert _count(db, IdempotencyRecord) == 0\n\n    valid_args = _args({})\n    first = _save_experience(db, principal, valid_args)\n    first_body = json.loads(first["content"][0]["text"])\n    assert first_body["saved"] is True\n    assert _count(db, V2Subject) == 3\n    assert _count(db, SubjectRelationship) == 2\n    assert _count(db, V2Experience) == 1\n    assert _count(db, IdempotencyRecord) == 1\n\n    replay = _save_experience(db, principal, valid_args)\n    replay_body = json.loads(replay["content"][0]["text"])\n    assert replay_body["experience_id"] == first_body["experience_id"]\n    assert _count(db, V2Subject) == 3\n    assert _count(db, SubjectRelationship) == 2\n    assert _count(db, V2Experience) == 1\n    assert _count(db, IdempotencyRecord) == 1\n''')
