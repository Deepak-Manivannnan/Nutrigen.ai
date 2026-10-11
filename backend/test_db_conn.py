import asyncio
from backend.app.db.session import init_db, AsyncSessionLocal
from backend.app.db.models import User
from sqlalchemy import select

async def test_db():
    try:
        await init_db()
        print('Database initialized successfully')
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()
            print(f'Users in DB: {len(users)}')
    except Exception as e:
        print(f'DB Error: {e}')

if __name__ == "__main__":
    asyncio.run(test_db())