"""Profile body-measurement limits apply to both creating and updating a profile."""
import pytest
from pydantic import ValidationError

from backend.app.schemas.api import ProfileCreate, ProfileResponse, ProfileUpdate

BASE = dict(
    name="A", age=30, sex="female", height_cm=165, weight_kg=60,
    activity_level="low", diet_type="veg",
)


@pytest.mark.parametrize("field,bad", [
    ("age", 1000), ("age", 17), ("age", 0), ("age", 121), ("age", -5),
    ("height_cm", 5), ("height_cm", 99), ("height_cm", 251), ("height_cm", 1000),
    ("weight_kg", 0.1), ("weight_kg", 29), ("weight_kg", 301), ("weight_kg", -10),
])
def test_out_of_range_values_are_rejected_on_create_and_update(field, bad):
    with pytest.raises(ValidationError):
        ProfileCreate(**{**BASE, field: bad})
    with pytest.raises(ValidationError):
        ProfileUpdate(**{field: bad})


@pytest.mark.parametrize("field,good", [
    ("age", 18), ("age", 120), ("height_cm", 100), ("height_cm", 250),
    ("weight_kg", 30), ("weight_kg", 300), ("age", 28), ("height_cm", 175.5),
])
def test_edges_and_normal_values_are_accepted(field, good):
    assert getattr(ProfileCreate(**{**BASE, field: good}), field) == good
    assert getattr(ProfileUpdate(**{field: good}), field) == good


def test_update_can_still_change_one_field_at_a_time():
    update = ProfileUpdate(diet_type="vegan")
    assert update.model_dump(exclude_unset=True) == {"diet_type": "vegan"}


def test_update_rejects_blank_or_huge_name():
    with pytest.raises(ValidationError):
        ProfileUpdate(name="")
    with pytest.raises(ValidationError):
        ProfileUpdate(name="x" * 101)


def test_reading_an_old_profile_with_odd_stored_numbers_still_works():
    # Responses are not range-checked, so a row saved before these limits
    # existed cannot make the profile endpoints fail.
    from datetime import datetime
    ProfileResponse(
        id=1, name="Old", age=15, height_cm=60, weight_kg=12, dislikes=[],
        allergies=[], created_at=datetime.now(), updated_at=datetime.now(),
    )
