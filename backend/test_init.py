import asyncio
import sys
sys.path.insert(0, '.')
from backend.app.db.session import init_db

async def test():
    await init_db()
    print('init_db completed')

asyncio.run(test())