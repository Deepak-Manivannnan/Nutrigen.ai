from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.config import settings
from backend.app.db.session import init_db
from backend.app.api.routes.auth import router as auth_router
from backend.app.api.routes.profile import router as profile_router
from backend.app.api.routes.chat import router as chat_router
from backend.app.api.routes.plans import router as plans_router
from backend.app.api.routes.trace import router as trace_router
from backend.app.api.streaming import router as streaming_router
from backend.app.observability.logging import setup_logging

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize structured logging and DB tables on startup
    setup_logging()
    if settings.is_production and not settings.cors_origins:
        logging.getLogger("app").warning(
            "FRONTEND_ORIGINS is not set: in production no website is allowed "
            "to call this API, so the frontend will be blocked by the browser."
        )
    await init_db()
    yield

# The interactive API docs list every endpoint, so they stay off in production.
_docs_kwargs = (
    {"docs_url": None, "redoc_url": None, "openapi_url": None}
    if settings.is_production else {}
)

app = FastAPI(
    title=settings.app_name,
    description="Multi-Agent System for Safe Meal Planning and Grocery Cost Estimation",
    version="1.0.0",
    lifespan=lifespan,
    **_docs_kwargs,
)

# CORS: only the real frontend may call the API from a browser. The session
# token travels in the Authorization header (not a cookie), so credentials are
# not needed. Local development additionally accepts any localhost port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=None if settings.is_production else r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Mount routes
app.include_router(auth_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(plans_router, prefix="/api")
app.include_router(trace_router, prefix="/api")
app.include_router(streaming_router, prefix="/api")

@app.get("/health")
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
