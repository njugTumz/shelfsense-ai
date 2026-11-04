"""FIELD backend entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.missions import router as missions_router
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.worker import Worker, recover_stale_state

    worker = None
    if get_settings().build_async:
        recover_stale_state()
        worker = Worker()
        worker.start()
        app.state.worker = worker
    yield
    if worker is not None:
        worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="FIELD API",
        description="Backend API for FIELD: turn real-world observations into working software.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )
    app.include_router(health_router)
    app.include_router(missions_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_env == "development",
    )
