from typing import Literal
from pydantic import Field
from app.schemas.common import StrictModel

class RecipeReviewData(StrictModel):
    overall_rating: float | None = Field(default=None, ge=0, le=10)
    flavour: float | None = Field(default=None, ge=0, le=10)
    instruction_clarity: float | None = Field(default=None, ge=0, le=10)
    preparation_time_accuracy: float | None = Field(default=None, ge=0, le=10)
    ingredient_availability: float | None = Field(default=None, ge=0, le=10)
    difficulty: float | None = Field(default=None, ge=0, le=10)
    repeat_worthiness: float | None = Field(default=None, ge=0, le=10)
    modifications: list[str] = []

class RestaurantReviewData(StrictModel):
    food: float | None = Field(default=None, ge=0, le=10)
    service: float | None = Field(default=None, ge=0, le=10)
    atmosphere: float | None = Field(default=None, ge=0, le=10)
    value: float | None = Field(default=None, ge=0, le=10)
    noise: float | None = Field(default=None, ge=0, le=10)
    meal_pacing: float | None = Field(default=None, ge=0, le=10)

DOMAIN_MODELS = {
    "recipe": RecipeReviewData,
    "restaurant": RestaurantReviewData,
}
