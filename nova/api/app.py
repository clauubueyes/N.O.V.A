from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from nova import __version__
from nova.agents import Agent, agent_presets, create_agent, get_preset
from nova.api.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    AgentInfoOut,
    ChatRequest,
    ChatResponse,
    ModelInfoOut,
    RememberRequest,
    SessionChatRequest,
    SessionChatResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionRunRequest,
    ToolInfoOut,
)
from nova.core.audit import AuditLog
from nova.core.config import NovaSettings, load_settings
from nova.core.logging import get_logger
from nova.core.session import ChatSession
from nova.llm.base import (
    ChatCompletionRequest,
    ChatMessage,
    LLMProvider,
    NOVAProviderError,
)
from nova.llm.registry import create_provider
from nova.llm.router import ModelRouter, build_router
from nova.memory import MemorySearchTool, MemoryService, MemoryStore, RememberTool
from nova.tools import registry as base_tools_registry
from nova.tools.host import all_host_tools
from nova.tools.permissions import PermissionSystem
from nova.tools.registry import create_registry
from nova.tools.runner import ToolRunner
from nova.tools.web import all_web_tools

logger = get_logger("api.app")
_STATIC_DIR = Path(__file__).parent / "static"


@dataclass
class SessionEntry:
    session: ChatSession
    memory: MemoryService
    runner: ToolRunner
    model: str
    agent: Agent | None = None

    def close(self) -> None:
        self.memory.close()


@dataclass
class AppState:
    settings: NovaSettings
    provider: LLMProvider
    audit: AuditLog
    permissions: PermissionSystem
    sessions: dict[str, SessionEntry] = field(default_factory=dict)
    agents: dict[str, SessionEntry] = field(default_factory=dict)


def _tool_infos(settings: NovaSettings) -> list[ToolInfoOut]:
    infos = [
        ToolInfoOut(name=tool.name, description=tool.description)
        for tool in base_tools_registry.all()
    ]
    memory_names = {tool.name for tool in (RememberTool, MemorySearchTool)}
    infos = [info for info in infos if info.name not in memory_names]
    for tool_cls in (RememberTool, MemorySearchTool):
        infos.append(ToolInfoOut(name=tool_cls.name, description=tool_cls.description))
    if settings.api.host_enabled:
        for tool in all_host_tools(settings.host):
            infos.append(ToolInfoOut(name=tool.name, description=tool.description))
    for tool in all_web_tools(settings.web):
        infos.append(ToolInfoOut(name=tool.name, description=tool.description))
    return infos


def _auth_middleware(settings: NovaSettings):
    """Reject any /v1/* request without a valid Bearer token when one is configured."""
    token = settings.api.token

    async def middleware(request: Request, call_next):
        if token and request.url.path.startswith("/v1/"):
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {token}":
                return JSONResponse(status_code=401, content={"detail": "unauthorized"})
        return await call_next(request)

    return middleware


def create_app(
    settings: NovaSettings | None = None,
    *,
    provider: LLMProvider | None = None,
) -> FastAPI:
    """Build the N.O.V.A. REST API reusing the Core (LLM, sessions, memory, tools)."""
    settings = settings or load_settings()

    if settings.api.host_enabled and not settings.api.token:
        raise ValueError(
            "api.host_enabled requires api.token: never expose the Desktop Agent "
            "host tools through the API without a Bearer token."
        )

    provider = provider or create_provider(settings.llm)

    state = AppState(
        settings=settings,
        provider=provider,
        audit=AuditLog(settings.audit.file),
        permissions=PermissionSystem(settings.permissions),
    )
    state.router: ModelRouter = build_router(settings.llm, settings.model_router)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        provider.close()
        for entry in state.sessions.values():
            entry.close()
        for entry in state.agents.values():
            entry.close()

    app = FastAPI(
        title="N.O.V.A.",
        description="Neural Operations & Virtual Assistant - REST API",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(_auth_middleware(settings))
    app.state.nova = state

    def build_session(session_id: str, agent_name: str | None = None) -> SessionEntry:
        session = ChatSession(
            max_history_messages=settings.session.max_history_messages,
            system_prompt=settings.session.system_prompt,
        )
        memory = MemoryService(
            MemoryStore(settings.memory.db_file),
            embed=provider.embed_text if provider.supports_embedding else None,
            session_id=f"api-{session_id}",
            max_context=settings.memory.max_context,
            similarity_threshold=settings.memory.similarity_threshold,
        )
        web_tools = all_web_tools(settings.web)
        registry = create_registry(
            [RememberTool(memory), MemorySearchTool(memory)]
            + (all_host_tools(settings.host) if settings.api.host_enabled else [])
            + web_tools,
            base=base_tools_registry,
        )
        runner = ToolRunner(
            registry=registry,
            permissions=state.permissions,
            audit=state.audit,
            confirm=lambda _question: False,  # the API never asks interactively
        )
        model = settings.llm.default_model
        agent = None
        if agent_name:
            get_preset(agent_name)
            agent = create_agent(
                agent_name,
                provider=provider,
                runner=runner,
                memory=memory,
                model=model,
                session=session,
            )
        return SessionEntry(
            session=session,
            memory=memory,
            runner=runner,
            model=model,
            agent=agent,
        )

    def get_session(session_id: str) -> SessionEntry:
        entry = state.sessions.get(session_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="session not found")
        return entry

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok" if provider.health() else "degraded",
            "provider": provider.health(),
            "version": __version__,
        }

    @app.get("/v1/models", response_model=list[ModelInfoOut])
    def list_models() -> list[ModelInfoOut]:
        try:
            return [
                ModelInfoOut(name=m.name, size=m.size, modified_at=m.modified_at)
                for m in provider.list_models()
            ]
        except NOVAProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/v1/tools", response_model=list[ToolInfoOut])
    def list_tools() -> list[ToolInfoOut]:
        return _tool_infos(settings)

    @app.post("/v1/route")
    def route(req: ChatRequest) -> dict[str, str]:
        text = req.messages[-1].content if req.messages else ""
        decision = state.router.route_for(text)
        return {
            "task_kind": decision.task_kind,
            "role": decision.role,
            "model": decision.model,
            "reason": decision.reason,
        }

    def get_agent_entry(name: str) -> SessionEntry:
        entry = state.agents.get(name)
        if entry is not None:
            return entry
        try:
            entry = build_session(f"agent-{name}", agent_name=name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        state.agents[name] = entry
        return entry

    @app.get("/v1/agents", response_model=list[AgentInfoOut])
    def list_agents() -> list[AgentInfoOut]:
        return [AgentInfoOut(name=p.name, description=p.description) for p in agent_presets()]

    @app.post("/v1/agents/{name}/chat", response_model=AgentChatResponse)
    def agent_chat(name: str, req: AgentChatRequest) -> AgentChatResponse:
        entry = get_agent_entry(name)
        try:
            result = entry.agent.act(req.message)
        except NOVAProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        entry.model = result.model
        return AgentChatResponse(
            agent=name,
            reply=result.answer,
            model=result.model,
            steps=[step.to_dict() for step in result.steps],
        )

    @app.post("/v1/chat", response_model=ChatResponse)
    def chat(req: ChatRequest) -> ChatResponse:
        try:
            response = provider.chat(
                ChatCompletionRequest(
                    messages=[ChatMessage(role=m.role, content=m.content) for m in req.messages],
                    model=req.model,
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                )
            )
        except NOVAProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return ChatResponse(
            id=uuid.uuid4().hex,
            model=response.model,
            message=response.message.to_dict(),
            usage=response.usage,
        )

    @app.post("/v1/sessions", response_model=SessionCreateResponse)
    def create_session(req: SessionCreateRequest | None = None) -> SessionCreateResponse:
        req = req or SessionCreateRequest()
        session_id = uuid.uuid4().hex
        try:
            entry = build_session(session_id, agent_name=req.agent)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        state.sessions[session_id] = entry
        logger.info("api session created: %s agent=%s", session_id, entry.agent.name if entry.agent else "")
        return SessionCreateResponse(
            session_id=session_id,
            model=entry.model,
            agent=entry.agent.name if entry.agent else "",
        )

    @app.post("/v1/sessions/{session_id}/chat", response_model=SessionChatResponse)
    def session_chat(session_id: str, req: SessionChatRequest) -> SessionChatResponse:
        entry = get_session(session_id)
        if entry.agent is not None:
            try:
                result = entry.agent.act(req.message)
            except NOVAProviderError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            entry.model = result.model
            return SessionChatResponse(
                session_id=session_id,
                reply=result.answer,
                model=result.model,
                context="",
                agent=entry.agent.name,
                steps=[step.to_dict() for step in result.steps],
            )
        entry.session.add_user(req.message)
        entry.memory.record("user", req.message)
        if req.model:
            model = req.model
        else:
            model = state.router.route_for(req.message).model
        try:
            request = entry.session.build_request(model=model)
            context = entry.memory.context(req.message)
            if context:
                request = ChatCompletionRequest(
                    messages=request.messages[:-1]
                    + [ChatMessage(role="system", content=context)]
                    + [request.messages[-1]],
                    model=request.model,
                )
            response = provider.chat(request)
        except NOVAProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        answer = response.message.content
        entry.session.add_assistant(answer)
        entry.memory.record("assistant", answer)
        entry.model = response.model
        return SessionChatResponse(
            session_id=session_id,
            reply=answer,
            model=response.model,
            context=context,
        )

    @app.get("/v1/sessions/{session_id}/messages")
    def session_messages(session_id: str) -> dict[str, Any]:
        entry = get_session(session_id)
        return {
            "session_id": session_id,
            "messages": [message.to_dict() for message in entry.session.messages()],
        }

    @app.delete("/v1/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, str]:
        entry = state.sessions.pop(session_id, None)
        if entry is None:
            raise HTTPException(status_code=404, detail="session not found")
        entry.close()
        logger.info("api session deleted: %s", session_id)
        return {"deleted": session_id}

    @app.post("/v1/sessions/{session_id}/run")
    def session_run(session_id: str, req: SessionRunRequest) -> dict[str, Any]:
        entry = get_session(session_id)
        result = entry.runner.run(req.tool, req.args)
        return {"session_id": session_id, "result": result.to_dict()}

    @app.post("/v1/sessions/{session_id}/remember")
    def session_remember(session_id: str, req: RememberRequest) -> dict[str, Any]:
        entry = get_session(session_id)
        memory_id = entry.memory.remember(req.content, kind=req.kind, source=req.source)
        return {"session_id": session_id, "id": memory_id, "content": req.content}

    @app.get("/v1/sessions/{session_id}/memory")
    def session_memory(session_id: str, q: str | None = None) -> dict[str, Any]:
        entry = get_session(session_id)
        if q:
            hits = entry.memory.search(q)
            return {
                "session_id": session_id,
                "type": "search",
                "hits": [hit.to_dict() for hit in hits],
            }
        records = entry.memory.recent_memories(limit=20)
        return {
            "session_id": session_id,
            "type": "recent",
            "memories": [
                {
                    "id": record.id,
                    "content": record.content,
                    "kind": record.kind,
                    "source": record.source,
                    "created_at": record.created_at,
                }
                for record in records
            ],
        }

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    return app