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


def test_recipe_root_is_stable_even_if_bad_root_arrives_first(db: Session):
    placement = resolve_concept_path(db, "dining.recipe.review")
    assert placement["status"] == "reuse"
    assert placement["path"] == "food.recipe.review"


def test_existing_recipe_word_is_reused_before_new_root_is_created(db: Session):
    add_concept(db, "food.recipe.review")
    placement = resolve_concept_path(db, "dining.recipe.review")
    assert placement["status"] == "reuse"
    assert placement["path"] == "food.recipe.review"
    assert placement["matched_word"] == "recipe"


def test_train_station_does_not_capture_radio_station(db: Session):
    add_concept(db, "transportation.train.station.review")
    placement = resolve_concept_path(db, "media.radio.station.review")
    assert placement["status"] == "new"
    assert placement["path"] == "media.radio.station.review"


def test_radio_station_does_not_capture_train_station(db: Session):
    add_concept(db, "media.radio.station.review")
    placement = resolve_concept_path(db, "transportation.train.station.review")
    assert placement["status"] == "new"
    assert placement["path"] == "transportation.train.station.review"


def test_same_parent_context_can_reuse_deeper_word(db: Session):
    add_concept(db, "transportation.train.station.review")
    placement = resolve_concept_path(db, "travel.train.station.review")
    assert placement["status"] == "reuse"
    assert placement["path"] == "transportation.train.station.review"
    assert placement["matched_word"] == "station"


def test_car_park_does_not_collide_with_leisure_park(db: Session):
    add_concept(db, "leisure.park.review")
    placement = resolve_concept_path(db, "transportation.car.park.review")
    assert placement["status"] == "new"
    assert placement["path"] == "transportation.car.park.review"


def test_airport_terminal_does_not_capture_computer_terminal(db: Session):
    add_concept(db, "transportation.airport.terminal.review")
    placement = resolve_concept_path(db, "computing.terminal.review")
    assert placement["status"] == "new"
    assert placement["path"] == "computing.terminal.review"


def test_known_dentist_root_is_stable(db: Session):
    placement = resolve_concept_path(db, "services.dentist.review")
    assert placement["status"] == "reuse"
    assert placement["path"] == "healthcare.dentist.review"


def test_unknown_direct_subject_under_other_root_requires_revision(db: Session):
    add_concept(db, "transportation.station.review")
    placement = resolve_concept_path(db, "broadcasting.station.review")
    assert placement["status"] == "revise"
    assert placement["path"] is None
    assert placement["candidates"] == ["transportation.station.review"]
