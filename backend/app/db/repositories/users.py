from typing import Optional, List
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import User, UserAllergy
from backend.app.schemas.api import ProfileCreate, ProfileUpdate
from backend.app.guardrails.calorie_rules import calculate_mifflin_st_jeor

class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, profile: ProfileCreate) -> User:
        user = User(
            name=profile.name,
            age=profile.age,
            sex=profile.sex,
            height_cm=profile.height_cm,
            weight_kg=profile.weight_kg,
            activity_level=profile.activity_level,
            goal=profile.goal,
            diet_type=profile.diet_type,
            dislikes=profile.dislikes,
        )
        self.session.add(user)
        await self.session.flush()

        for allergen in profile.allergies:
            allergy_record = UserAllergy(user_id=user.id, allergen=allergen.strip().lower())
            self.session.add(allergy_record)

        await self.session.commit()
        await self.session.refresh(user)

        # Calculate and persist calorie range after user is created
        await self._calculate_and_persist_calories(user)
        return await self.get_by_id(user.id)

    async def get_by_id(self, user_id: int) -> Optional[User]:
        stmt = (
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.allergies))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        stmt = (
            select(User)
            .where(User.email == email.strip().lower())
            .options(selectinload(User.allergies))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_account(self, name: str, email: str, password_hash: str) -> User:
        user = User(
            name=name.strip(),
            email=email.strip().lower(),
            password_hash=password_hash,
            # Profile fields left as None - will be set during onboarding
            age=None,
            sex=None,
            height_cm=None,
            weight_kg=None,
            activity_level=None,
            goal=None,
            diet_type=None,
            dislikes=[],
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return await self.get_by_id(user.id)

    async def set_password_hash(self, user: User, new_hash: str) -> None:
        """Store a freshly computed password hash (used to upgrade old hashes)."""
        user.password_hash = new_hash
        await self.session.commit()

    async def get_first(self) -> Optional[User]:
        stmt = select(User).order_by(User.id.asc()).options(selectinload(User.allergies))
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def _calculate_and_persist_calories(self, user: User) -> None:
        """Calculate and persist calorie range for a user based on their profile."""
        cal_info = calculate_mifflin_st_jeor(
            age=user.age,
            sex=user.sex,
            height_cm=user.height_cm,
            weight_kg=user.weight_kg,
            activity_level=user.activity_level,
            goal=user.goal or "maintain"
        )
        user.bmr = cal_info["bmr"]
        user.tdee = cal_info["tdee"]
        user.min_safe_calories = cal_info["min_safe_calories"]
        user.max_safe_calories = cal_info["max_safe_calories"]
        user.suggested_target_calories = cal_info["suggested_target"]
        await self.session.commit()
        await self.session.refresh(user)

    async def update(self, user_id: int, updates: ProfileUpdate) -> Optional[User]:
        user = await self.get_by_id(user_id)
        if not user:
            return None

        update_dict = updates.model_dump(exclude_unset=True)
        allergies = update_dict.pop("allergies", None)

        for key, value in update_dict.items():
            setattr(user, key, value)

        if allergies is not None:
            # Replace allergies
            user.allergies.clear()
            for allergen in allergies:
                user.allergies.append(UserAllergy(allergen=allergen.strip().lower()))

        await self.session.commit()
        await self.session.refresh(user)

        # Recalculate and persist calorie range if relevant fields were updated
        calorie_fields = {"age", "sex", "height_cm", "weight_kg", "activity_level", "goal"}
        if any(field in updates.model_fields_set for field in calorie_fields):
            await self._calculate_and_persist_calories(user)

        return user
