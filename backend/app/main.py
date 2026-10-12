from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.config import settings
from backend.app.db.session import init_db
from backend.app.db.seed.seed import seed_allergen_sources
from backend.app.api.routes.auth import router as auth_router
from backend.app.api.routes.profile import router as profile_router
from backend.app.api.routes.chat import router as chat_router
from backend.app.api.routes.plans import router as plans_router
from backend.app.api.routes.trace import router as trace_router
from backend.app.api.streaming import router as streaming_router
from backend.app.observability.logging import setup_logging

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize structured logging, DB tables, and seed data on startup
    setup_logging()
    await init_db()
    await seed_allergen_sources()
    yield

app = FastAPI(
    title=settings.app_name,
    description="Multi-Agent System for Safe Meal Planning and Grocery Cost Estimation",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for React frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routes
app.include_router(auth_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(plans_router, prefix="/api")
app.include_router(trace_router, prefix="/api")
app.include_router(streaming_router, prefix="/api")

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "app": settings.app_name,
        "environment": settings.app_env
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
