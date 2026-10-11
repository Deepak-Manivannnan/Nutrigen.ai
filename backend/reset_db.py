import asyncio
import sys
sys.path.insert(0, '.')
from backend.app.db.session import engine, Base
from sqlalchemy import text

async def test():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        print('Tables dropped')
        await conn.run_sync(Base.metadata.create_all)
        print('Tables created')
        migrations = [
            'ALTER TABLE users ADD COLUMN bmr FLOAT',
            'ALTER TABLE users ADD COLUMN tdee FLOAT',
            'ALTER TABLE users ADD COLUMN min_safe_calories FLOAT',
            'ALTER TABLE users ADD COLUMN max_safe_calories FLOAT',
            'ALTER TABLE users ADD COLUMN suggested_target_calories FLOAT',
        ]
        for stmt in migrations:
            try:
                await conn.execute(text(stmt))
                print(f'Migration: {stmt}')
            except Exception as e:
                print(f'Migration skipped: {stmt} - {e}')
        print('Migrations completed')

asyncio.run(test())