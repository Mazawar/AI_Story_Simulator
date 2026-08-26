"""FastAPI 应用工厂：本地服务（仅 127.0.0.1 + 一次性 token）。

- /api/* 走 token 鉴权（X-Auth-Token 头或 ?token= 查询参数，SSE 用后者）；
- 发布模式下托管 web/dist/ 静态前端（前端用 hash 路由，无需 SPA 回退）。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config
from ..db import Database, migrate
from .routers import router


def create_app(*, token: str, dry_run: bool = False,
               db_path=None, web_dist=None) -> FastAPI:
    app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION, docs_url=None,
                  redoc_url=None)
    app.state.token = token
    app.state.dry_run = dry_run
    app.state.backend_name = "dry-run" if dry_run else "auto"

    db = Database(db_path or config.DB_PATH)
    migrate(db)
    app.state.db = db

    @app.middleware("http")
    async def token_auth(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("X-Auth-Token") or request.query_params.get("token")
            if supplied != app.state.token:
                return JSONResponse({"detail": "unauthorized"}, status_code=403)
        return await call_next(request)

    @app.on_event("shutdown")
    def _close_db():
        db.close()

    app.include_router(router, prefix="/api")

    dist = web_dist or config.WEB_DIST_DIR
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")
    return app
