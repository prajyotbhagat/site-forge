"""SiteForge API + customer web app."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import audit, db as dbmod
from .api import auth, design_api, owner, projects, public, release_api
from .api.owner import purge_expired
from .lifecycle import InvalidTransition

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    dbmod.init_engine()
    s = dbmod.SessionLocal()
    try:
        purge_expired(s)  # retention: run on startup; schedule daily in production
        s.commit()
    finally:
        s.close()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="SiteForge", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def correlation_and_headers(request: Request, call_next):
        cid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = audit.correlation_id.set(cid)
        try:
            response = await call_next(request)
        finally:
            audit.correlation_id.reset(token)
        response.headers["X-Request-ID"] = cid
        path = request.url.path
        if not path.startswith(("/sites/", "/preview/")):
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "same-origin")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-src 'self'; "
                "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        if path.startswith("/api/") and not path.startswith("/api/public/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(InvalidTransition)
    async def _invalid_transition(_req: Request, exc: InvalidTransition):
        return JSONResponse({"detail": "That step isn't available right now. Please refresh and try again."}, 409)

    for r in (auth.router, projects.router, release_api.router, release_api.preview_router, owner.router, public.router, design_api.router):
        app.include_router(r)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
