import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import User
from app.models.v2 import SubjectRelationship, SubjectType, SubjectTypeField, V2Experience
from app.schemas.v2 import (
    ExperienceCreate, FieldEnsure, SubjectContextEnsure, SubjectEnrichmentCheck, SubjectEnsure,
)
from app.services.v2 import (
    add_subject_type_alias, add_type_relationship, create_experience, delete_owned_experience,
    descendant_type_ids,
    ensure_field, ensure_subject, ensure_subject_context, ensure_subject_type, normalise_term,
    resolve_subject_type,
)


@pytest.fixture()
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:");Base.metadata.create_all(engine)
    with Session(engine) as session:yield session


@pytest.mark.parametrize(("entered","expected"),[("ferry","ferry"),("Ferries","ferry"),("ferry's","ferry"),("RECIPES","recipe")])
def test_mechanical_variants_share_lookup_key(entered,expected):
    assert normalise_term(entered)==expected


def test_unknown_type_is_created_once_and_reused_by_plural(db):
    first,created,_=ensure_subject_type(db,"ferry",created_by="client-a")
    second,created_again,method=ensure_subject_type(db,"ferries",created_by="client-b")
    assert created is True and created_again is False
    assert first.id==second.id and method=="canonical"
    assert db.scalar(select(SubjectType).where(SubjectType.normalized_name=="ferry")) is first


def test_aliases_standardise_language_without_becoming_relationships(db):
    ferry,_,_=ensure_subject_type(db,"ferry",created_by="test")
    add_subject_type_alias(db,ferry,"ferry crossing",source="test")
    assert resolve_subject_type(db,"ferry crossings").id==ferry.id


def test_relationship_is_editable_metadata_not_storage_address(db):
    ferry,_,_=ensure_subject_type(db,"ferry",created_by="test")
    transport,_,_=ensure_subject_type(db,"transportation",created_by="test")
    add_type_relationship(db,ferry,"belongs_to",transport,source="test")
    assert descendant_type_ids(db,transport)=={transport.id,ferry.id}
    assert ferry.canonical_name=="ferry"


def test_review_stores_stable_type_id_and_standardised_fields(db):
    user=User(display_name="Test",profile_data={});db.add(user);db.commit();db.refresh(user)
    ferry,_,_=ensure_subject_type(db,"ferry",created_by="test")
    ensure_field(db,FieldEnsure(canonical_name="rating",json_schema={"type":"integer","minimum":1,"maximum":5},aliases=["stars"],subject_types=["ferry"]),source="test")
    subject=ensure_subject(db,SubjectEnsure(subject_type="ferries",name="Newhaven to Dieppe",canonical_key="newhaven-dieppe"))
    exp=create_experience(db,ExperienceCreate(owner_id=user.id,subject_id=subject.id,headline="Smooth crossing",summary="Comfortable trip",raw_text="The crossing was comfortable.",structured_data={"stars":4},user_approved=True),"test-client")
    assert subject.subject_type_id==ferry.id
    assert exp.record_type=="review"
    assert exp.structured_data=={"rating":4}
    assert exp.normalization_log[0]["method"]=="alias"


def test_unknown_structured_field_does_not_create_schema_from_one_review(db):
    user=User(display_name="Test",profile_data={});db.add(user);db.commit();db.refresh(user)
    ensure_subject_type(db,"ferry",created_by="test")
    subject=ensure_subject(db,SubjectEnsure(subject_type="ferry",name="Crossing",canonical_key="crossing"))
    with pytest.raises(ValueError,match="Preserve a one-off detail in raw_text"):
        create_experience(db,ExperienceCreate(owner_id=user.id,subject_id=subject.id,headline="Trip",summary="Trip",raw_text="Nice café.",structured_data={"cafe wallpaper colour":"blue"},user_approved=True),"test")


def test_review_can_add_generic_unreviewed_subject_context(db):
    user=User(display_name="Test",profile_data={});db.add(user);db.commit();db.refresh(user)
    ensure_subject_type(db,"cafe",created_by="test")
    ensure_subject_type(db,"organization",created_by="test")
    reviewed=ensure_subject(
        db,
        SubjectEnsure(
            subject_type="cafe",name="Example Cafe — Lechlade",
            canonical_key="example-cafe-lechlade",
            attributes={"town":"Lechlade"},
        ),
    )
    context=ensure_subject_context(
        db,
        reviewed,
        SubjectContextEnsure.model_validate({
            "subjects":[
                {
                    "ref":"brand","subject_type":"organization","name":"Example Cafe",
                    "canonical_key":"example-cafe",
                    "identifiers":{"website":"https://example.test/locations"},
                    "provenance":{"source_url":"https://example.test/locations"},
                },
                {
                    "ref":"cirencester","subject_type":"cafe",
                    "name":"Example Cafe — Cirencester",
                    "canonical_key":"example-cafe-cirencester",
                    "attributes":{"town":"Cirencester","address":"1 Example Street"},
                    "provenance":{"source_url":"https://example.test/locations"},
                },
            ],
            "relationships":[
                {
                    "source_ref":"reviewed_subject","relationship":"branch_of",
                    "target_ref":"brand",
                    "provenance":{"source_url":"https://example.test/locations"},
                },
                {
                    "source_ref":"cirencester","relationship":"branch_of",
                    "target_ref":"brand",
                    "provenance":{"source_url":"https://example.test/locations"},
                },
            ],
        }),
        client_id="test",
    )
    exp=create_experience(
        db,
        ExperienceCreate(
            owner_id=user.id,subject_id=reviewed.id,headline="Good lunch",
            summary="Good lunch",raw_text="I enjoyed lunch here.",user_approved=True,
        ),
        "test",
    )

    assert exp.experienced_at is not None
    assert len(context["subjects"])==2
    assert len(context["relationships"])==2
    assert len(db.scalars(select(SubjectRelationship)).all())==2
    assert len(db.scalars(select(V2Experience)).all())==1


def test_existing_global_field_auto_attaches_on_first_valid_use(db):
    user=User(display_name="Test",profile_data={});db.add(user);db.commit();db.refresh(user)
    estate_agent,_,_=ensure_subject_type(db,"estate agent",created_by="test")
    rating=ensure_field(
        db,
        FieldEnsure(
            canonical_name="rating",
            json_schema={"type":"integer","minimum":1,"maximum":5},
            subject_types=[],
        ),
        source="test",
    )
    subject=ensure_subject(
        db,
        SubjectEnsure(
            subject_type="estate agent",name="Example Estate Agents",
            canonical_key="example-estate-agents",
        ),
    )

    exp=create_experience(
        db,
        ExperienceCreate(
            owner_id=user.id,subject_id=subject.id,headline="Disappointing service",
            summary="Poor communication",raw_text="Two stars.",structured_data={"rating":2},
            user_approved=True,
        ),
        "test-client",
    )

    attachment=db.scalar(select(SubjectTypeField).where(
        SubjectTypeField.subject_type_id==estate_agent.id,
        SubjectTypeField.field_id==rating.id,
    ))
    assert attachment is not None
    assert exp.structured_data=={"rating":2}
    assert exp.normalization_log==[{
        "submitted":"rating","canonical":"rating","field_id":str(rating.id),
        "method":"canonical","attached_to_subject_type":True,
    }]


def test_existing_subject_can_be_enriched_without_another_review(db):
    ensure_subject_type(db,"estate agent",created_by="test")
    ensure_subject_type(db,"organization",created_by="test")
    subject=ensure_subject(
        db,
        SubjectEnsure(
            subject_type="estate agent",name="Example Estate Agents, Stroud",
            canonical_key="example-estate-agents-stroud",
        ),
    )
    enriched=ensure_subject(
        db,
        SubjectEnsure(
            subject_type="estate agent",name=subject.name,
            canonical_key=subject.canonical_key,
            identifiers={"website":"https://example.test/stroud"},
            attributes={"town":"Stroud","address":"1 Example Street"},
            provenance={"source_url":"https://example.test/stroud"},
        ),
    )
    context=ensure_subject_context(
        db,
        enriched,
        SubjectContextEnsure.model_validate({
            "subjects":[{
                "ref":"brand","subject_type":"organization","name":"Example Estate Agents",
                "canonical_key":"example-estate-agents",
                "identifiers":{"website":"https://example.test"},
                "provenance":{"source_url":"https://example.test"},
            }],
            "relationships":[{
                "source_ref":"subject","relationship":"branch_of","target_ref":"brand",
                "provenance":{"source_url":"https://example.test/branches"},
            }],
        }),
        client_id="test",
    )

    assert enriched.id==subject.id
    assert enriched.identifiers_json["website"]=="https://example.test/stroud"
    assert enriched.attributes_json["town"]=="Stroud"
    assert enriched.provenance_json["source_url"]=="https://example.test/stroud"
    assert len(context["relationships"])==1
    assert len(db.scalars(select(V2Experience)).all())==0



def test_subject_enrichment_check_is_saved_in_review_provenance(db):
    user=User(display_name="Test",profile_data={});db.add(user);db.commit();db.refresh(user)
    ensure_subject_type(db,"website",created_by="test")
    subject=ensure_subject(
        db,SubjectEnsure(subject_type="website",name="Example Site",canonical_key="example-site")
    )
    exp=create_experience(
        db,
        ExperienceCreate(
            owner_id=user.id,subject_id=subject.id,headline="Useful site",summary="Useful",
            raw_text="This website was useful.",user_approved=True,
            subject_enrichment_check=SubjectEnrichmentCheck(
                status="completed",sources=["https://example.test/about"]
            ),
        ),
        "test-client",
    )
    check=exp.provenance["subject_enrichment_check"]
    assert check["status"]=="completed"
    assert check["sources"]==["https://example.test/about"]
    assert "recorded_at" in check



def test_user_can_delete_only_their_own_review_and_owned_orphan_subject(db):
    owner=User(display_name="Owner",profile_data={})
    other=User(display_name="Other",profile_data={})
    db.add_all([owner,other]);db.commit();db.refresh(owner);db.refresh(other)
    ensure_subject_type(db,"cafe",created_by="test")
    subject=ensure_subject(
        db,SubjectEnsure(subject_type="cafe",name="Owned Cafe",canonical_key="owned-cafe"),
        "test-client",owner_id=owner.id,
    )
    experience=create_experience(
        db,ExperienceCreate(
            owner_id=owner.id,subject_id=subject.id,headline="Review",summary="Review",
            raw_text="My review.",user_approved=True,
        ),"test-client",
    )

    with pytest.raises(ValueError,match="Experience not found"):
        delete_owned_experience(db,experience.id,other.id)

    result=delete_owned_experience(db,experience.id,owner.id)
    assert result["subject_deleted"] is True
    assert db.get(V2Experience,experience.id) is None
    assert db.get(type(subject),subject.id) is None


def test_deleting_own_review_preserves_a_shared_subject(db):
    first=User(display_name="First",profile_data={})
    second=User(display_name="Second",profile_data={})
    db.add_all([first,second]);db.commit();db.refresh(first);db.refresh(second)
    ensure_subject_type(db,"cafe",created_by="test")
    subject=ensure_subject(
        db,SubjectEnsure(subject_type="cafe",name="Shared Cafe",canonical_key="shared-cafe"),
        "test-client",owner_id=first.id,
    )
    first_review=create_experience(
        db,ExperienceCreate(
            owner_id=first.id,subject_id=subject.id,headline="First",summary="First",
            raw_text="First review.",user_approved=True,
        ),"test-client",
    )
    second_review=create_experience(
        db,ExperienceCreate(
            owner_id=second.id,subject_id=subject.id,headline="Second",summary="Second",
            raw_text="Second review.",user_approved=True,
        ),"test-client",
    )

    result=delete_owned_experience(db,first_review.id,first.id)
    assert result["subject_deleted"] is False
    assert db.get(type(subject),subject.id) is not None
    assert db.get(V2Experience,second_review.id) is not None
