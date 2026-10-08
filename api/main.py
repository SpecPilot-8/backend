from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import Settings
from api.database import Database
from api.errors import install_handlers
from api.routes import router


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    database = Database(config.database_url)

    @asynccontextmanager
    async def lifespan(app):
        config.data_dir.mkdir(parents=True, exist_ok=True)
        (config.data_dir / "uploads").mkdir(exist_ok=True)
        database.initialize()
        yield
        database.engine.dispose()

    app = FastAPI(
        title="SpecPilot API", version="0.1.0", lifespan=lifespan,
        description="프로젝트·문서·코드·수동 판정 API. AI 요청은 blocked 작업으로 누락 기능을 반환합니다.",
    )
    app.state.settings = config
    app.state.database = database
    app.add_middleware(CORSMiddleware, allow_origins=list(config.cors_origins),
                       allow_methods=["GET", "POST", "PUT", "OPTIONS"], allow_headers=["Content-Type"])
    install_handlers(app)
    app.include_router(router)
    return app


app = create_app()
