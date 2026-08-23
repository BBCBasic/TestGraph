from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.entities import PairwiseAlignment, SchemaDefinition, Subject, User
from app.schemas.domains import RecipeReviewData, RestaurantReviewData


def run():
    db = SessionLocal()
    try:
        schema_specs = [
            ("recipe", "1.0", "stable", RecipeReviewData.model_json_schema()),
            ("restaurant", "1.0", "stable", RestaurantReviewData.model_json_schema()),
        ]
        for subject_type, version, status, json_schema in schema_specs:
            schema = db.scalar(select(SchemaDefinition).where(
                SchemaDefinition.subject_type == subject_type,
                SchemaDefinition.version == version,
            ))
            if schema:
                schema.status = status
                schema.json_schema = json_schema
            else:
                db.add(SchemaDefinition(
                    subject_type=subject_type,
                    version=version,
                    status=status,
                    json_schema=json_schema,
                ))

        if not db.scalar(select(User).where(User.display_name == "Demo User A")):
            user_a = User(
                display_name="Demo User A",
                profile_data={"dimension_importance": {"noise": 0.9, "food": 0.9, "service": 0.8, "meal_pacing": 0.75}},
            )
            user_b = User(
                display_name="Demo User B",
                profile_data={"dimension_importance": {"noise": 0.15, "food": 0.95, "service": 0.75, "meal_pacing": 0.3}},
            )
            user_c = User(
                display_name="Demo User C",
                profile_data={"dimension_importance": {"noise": 0.95, "food": 0.8, "service": 0.85, "meal_pacing": 0.75}},
            )
            db.add_all([user_a, user_b, user_c])
            db.flush()
            db.add_all([
                Subject(
                    subject_type="restaurant",
                    name="Example Bistro",
                    canonical_key="example-bistro-demo",
                    metadata_json={"demo": True, "city": "Exampletown", "country": "GB"},
                ),
                Subject(
                    subject_type="recipe",
                    name="Example Rice Dish",
                    canonical_key="example-rice-dish-demo",
                    metadata_json={"demo": True},
                ),
                PairwiseAlignment(
                    source_user_id=user_a.id,
                    target_user_id=user_b.id,
                    dimensions={"noise": 0.2, "food": 0.75, "service": 0.7, "meal_pacing": 0.4},
                ),
                PairwiseAlignment(
                    source_user_id=user_a.id,
                    target_user_id=user_c.id,
                    dimensions={"noise": 0.9, "food": 0.7, "service": 0.8, "meal_pacing": 0.75},
                ),
            ])
        db.commit()
        print("Seed complete")
    finally:
        db.close()


if __name__ == "__main__":
    run()
