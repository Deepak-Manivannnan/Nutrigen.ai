from typing import Optional, List
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import User, UserAllergy
from backend.app.schemas.api import ProfileCreate, ProfileUpdate

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
            age=26,
            sex="female",
            height_cm=165.0,
            weight_kg=60.0,
            activity_level="medium",
            goal="maintain",
            diet_type="veg",
            dislikes=[],
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return await self.get_by_id(user.id)

    async def get_first(self) -> Optional[User]:
        stmt = select(User).order_by(User.id.asc()).options(selectinload(User.allergies))
        result = await self.session.execute(stmt)
        return result.scalars().first()

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
        return user
