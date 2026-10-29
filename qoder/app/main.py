"""FastAPI 后端：对前端暴露 /api/*，对 PowerContext 暴露 HTTP 客户端调用。

同时负责托管 ``static/`` 下的网页前端（单页应用）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__, question_bank
from app.config import BASE_DIR, Settings, load_settings
from app.memory_repository import MemoryRepository
from app.powercontext_client import PowerContextClient, PowerContextError
from app.review_service import ReviewService
from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    HealthResponse,
    OverviewResponse,
    SessionStartResponse,
    StartSessionRequest,
)

STATIC_DIR = BASE_DIR / "static"


def build_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """构造 FastAPI 应用。

    ``settings`` 与 ``transport`` 均可注入以便测试：``transport`` 会传给底层
    :class:`PowerContextClient`，从而把请求导向内存中的假 PowerContext。
    """

    cfg = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = PowerContextClient(cfg.powercontext_url, token=cfg.powercontext_token, transport=transport)
        repository = MemoryRepository(client, cfg.scope_prefix)
        app.state.settings = cfg
        app.state.client = client
        app.state.service = ReviewService(repository, default_size=cfg.session_size)
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(
        title="个性化复习助手",
        version=__version__,
        description="基于 PowerContext HTTP API 的个性化复习助手后端。",
        lifespan=lifespan,
    )
    # 前端与后端同源；开启宽松 CORS 以便直接用 file:// 打开页面时也能调试。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(PowerContextError)
    async def _powercontext_error_handler(_: Request, exc: PowerContextError) -> dict[str, object]:
        # 用 502 表示上游 PowerContext 不可用或返回错误
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    def get_service(request: Request) -> ReviewService:
        return request.app.state.service  # type: ignore[no-any-return]

    # ---------------------------------------------------------------- #
    # 元信息
    # ---------------------------------------------------------------- #
    @app.get("/api/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        client: PowerContextClient = request.app.state.client
        alive = await client.is_alive()
        return HealthResponse(
            app=f"review-assistant/{__version__}",
            powercontext_url=cfg.powercontext_url,
            powercontext_alive=alive,
            scope_prefix=cfg.scope_prefix,
        )

    @app.get("/api/subjects")
    async def subjects() -> dict[str, list[dict[str, object]]]:
        """返回题库覆盖的学科及其知识点，供前端下拉选择。"""

        items = [
            {
                "name": subject,
                "knowledge_points": question_bank.knowledge_points(subject),
                "count": len(question_bank.questions_for_subject(subject)),
            }
            for subject in question_bank.all_subjects()
        ]
        return {"subjects": items}

    # ---------------------------------------------------------------- #
    # 核心用例
    # ---------------------------------------------------------------- #
    @app.post("/api/session/start", response_model=SessionStartResponse)
    async def session_start(
        payload: StartSessionRequest,
        service: ReviewService = Depends(get_service),
    ) -> SessionStartResponse:
        if not question_bank.questions_for_subject(payload.subject):
            raise HTTPException(status_code=400, detail=f"题库暂不支持学科：{payload.subject}")
        return await service.start_session(payload.learner_id, payload.subject, payload.size)

    @app.post("/api/answer", response_model=AnswerResponse)
    async def answer(
        payload: AnswerRequest,
        service: ReviewService = Depends(get_service),
    ) -> AnswerResponse:
        return await service.submit_answer(payload)

    @app.get("/api/overview", response_model=OverviewResponse)
    async def overview(
        learner_id: str,
        service: ReviewService = Depends(get_service),
    ) -> OverviewResponse:
        if not learner_id.strip():
            raise HTTPException(status_code=400, detail="learner_id 不能为空")
        return await service.overview(learner_id)

    # ---------------------------------------------------------------- #
    # 前端页面
    # ---------------------------------------------------------------- #
    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # 静态资源挂载（放在路由注册之后，避免覆盖 /api 与 /）
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


app = build_app()
