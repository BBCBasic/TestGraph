from sqlalchemy import text

from app.db.session import SessionLocal

TARGET_KEY = "vehicle-wo68lcj"
TARGET_NAME = "Renault Zoe WO68 LCJ"


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT id, name, canonical_key
                FROM v2_subjects
                WHERE canonical_key = :key
                  AND lower(name) = lower(:name)
                """
            ),
            {"key": TARGET_KEY, "name": TARGET_NAME},
        ).mappings().all()

        if len(rows) != 1:
            raise RuntimeError(
                f"Refusing cleanup: expected exactly one target, found {len(rows)}"
            )

        subject_id = rows[0]["id"]

        checks = {
            "experiences": "SELECT count(*) FROM v2_experiences WHERE subject_id = :sid",
            "assessments": "SELECT count(*) FROM assessments WHERE subject_id = :sid",
            "location_assertions": "SELECT count(*) FROM location_assertions WHERE subject_id = :sid OR object_subject_id = :sid",
            "relationships": "SELECT count(*) FROM subject_relationships WHERE source_subject_id = :sid OR target_subject_id = :sid",
            "classification_decisions": "SELECT count(*) FROM subject_classification_decisions WHERE subject_id = :sid",
        }

        references = {
            label: db.execute(text(sql), {"sid": subject_id}).scalar_one()
            for label, sql in checks.items()
        }
        nonzero = {k: v for k, v in references.items() if v}
        if nonzero:
            raise RuntimeError(
                f"Refusing cleanup: rogue subject is not orphaned; references={nonzero}"
            )

        deleted = db.execute(
            text(
                """
                DELETE FROM v2_subjects
                WHERE id = :sid
                  AND canonical_key = :key
                  AND lower(name) = lower(:name)
                """
            ),
            {"sid": subject_id, "key": TARGET_KEY, "name": TARGET_NAME},
        )
        if deleted.rowcount != 1:
            raise RuntimeError(f"Refusing cleanup: delete affected {deleted.rowcount} rows")

        db.commit()

        remaining = db.execute(
            text("SELECT count(*) FROM v2_subjects WHERE canonical_key = :key"),
            {"key": TARGET_KEY},
        ).scalar_one()
        if remaining != 0:
            raise RuntimeError(
                f"Cleanup verification failed: {remaining} target row(s) remain"
            )

        print(f"Deleted rogue Zoe subject {subject_id}; verified canonical key absent")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
