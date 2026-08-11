import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import semantic  # noqa: F401
from app.models.v2 import Concept
from app.services.concept_placement import resolve_concept_path


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def add_concept(db: Session, path: str) -> Concept:
    parent = None
    built = []
    for part in path.split("."):
        built.append(part)
        current_path = ".".join(built)
        current = db.query(Concept).filter(Concept.path == current_path).one_or_none()
        if current is None:
            current = Concept(
                path=current_path,
                name=part,
                parent_id=parent.id if parent else None,
                status="active",
                created_by="test",
            )
            db.add(current)
            db.flush()
        parent = current
    db.commit()
    return parent


def test_first_recipe_review_keeps_sensible_domain_root(db: Session):
    placement = resolve_concept_path(db, "food.recipe.review")

    assert placement["status"] == "new"
    assert placement["path"] == "food.recipe.review"


def test_recipe_review_cannot_be_created_as_root_level_subject(db: Session):
    placement = resolve_concept_path(db, "recipe.review")

    assert placement["status"] == "revise"
    assert placement["path"] is None
    assert "food.recipe.review" in placement["reason"]


def test_existing_recipe_word_is_reused_before_new_root_is_created(db: Session):
    add_concept(db, "food.recipe.review")

    placement = resolve_concept_path(db, "dining.recipe.review")

    assert placement["status"] == "reuse"
    assert placement["path"] == "food.recipe.review"
    assert placement["matched_word"] == "recipe"


def test_existing_subject_node_can_be_extended_with_review(db: Session):
    add_concept(db, "food.recipe")

    placement = resolve_concept_path(db, "dining.recipe.review")

    assert placement["status"] == "reuse"
    assert placement["path"] == "food.recipe.review"


def test_same_word_in_multiple_meanings_requires_revision(db: Session):
    add_concept(db, "transportation.station")
    add_concept(db, "broadcasting.station")

    placement = resolve_concept_path(db, "places.station.review")

    assert placement["status"] == "revise"
    assert placement["path"] is None
    assert placement["candidates"] == [
        "broadcasting.station.review",
        "transportation.station.review",
    ]
