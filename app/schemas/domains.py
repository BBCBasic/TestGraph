from datetime import date
from typing import Literal

from pydantic import Field, field_validator

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


class RestaurantDishReview(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    rating: float | None = Field(default=None, ge=0, le=10)
    notes: str | None = Field(default=None, max_length=1000)
    shared: bool = False
    would_order_again: bool | None = None


class RestaurantReviewData(StrictModel):
    overall_rating: float | None = Field(default=None, ge=0, le=10)
    food: float | None = Field(default=None, ge=0, le=10)
    service: float | None = Field(default=None, ge=0, le=10)
    atmosphere: float | None = Field(default=None, ge=0, le=10)
    value: float | None = Field(default=None, ge=0, le=10)
    noise_comfort: float | None = Field(
        default=None, ge=0, le=10,
        description="Comfort with the noise level: 0 is unbearably noisy; 10 is acoustically comfortable.",
    )
    meal_pacing: float | None = Field(default=None, ge=0, le=10)
    drinks: float | None = Field(default=None, ge=0, le=10)
    visit_date: date | None = None
    meal_type: Literal["breakfast", "brunch", "lunch", "afternoon_tea", "dinner", "late_night", "other"] | None = None
    party_size: int | None = Field(default=None, ge=1, le=100)
    occasion: str | None = Field(default=None, max_length=200)
    wait_minutes: int | None = Field(default=None, ge=0, le=1440)
    spend_per_person: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    dishes: list[RestaurantDishReview] = []
    standout_dishes: list[str] = []
    disappointing_dishes: list[str] = []

    @field_validator("currency")
    @classmethod
    def uppercase_iso_currency(cls, value):
        if value is None:
            return value
        if not value.isalpha() or value != value.upper():
            raise ValueError("currency must be a three-letter uppercase ISO 4217 code")
        return value


DOMAIN_MODELS = {"recipe": RecipeReviewData, "restaurant": RestaurantReviewData}
