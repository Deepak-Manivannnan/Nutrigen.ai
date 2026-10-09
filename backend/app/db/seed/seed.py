from backend.app.db.session import init_db

async def seed_allergen_sources():
    """No-op: allergen database table replaced by hybrid architecture."""
    await init_db()
    return

