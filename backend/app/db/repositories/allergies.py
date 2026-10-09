from typing import List
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import UserAllergy

class AllergyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_allergies(self, user_id: int) -> List[str]:
        stmt = select(UserAllergy.allergen).where(UserAllergy.user_id == user_id)
        result = await self.session.execute(stmt)
        return [row[0].lower() for row in result.all()]
