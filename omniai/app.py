"""Production application factory.

Builds the fully wired gateway from environment settings — engine (attached
to the external serving container), DB-backed interaction buffer, continuous
learner, guardrails, and a default chat graph. This is the container
entrypoint:

    uvicorn omniai.app:create_app --factory --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

from fastapi import FastAPI

from omniai.engine import ModelEngine
from omniai.gateway import GatewayRouter
from omniai.graph import END, START, Graph, State
from omniai.guardrails import PromptGuard
from omniai.logging import get_logger
from omniai.memory import ContinuousLearner, InteractionBuffer, LoRATrainer
from omniai.settings import OmniSettings, get_settings

logger = get_logger(__name__)


def build_chat_graph(engine: ModelEngine) -> Graph:
    """Default single-node chat graph; replace with your own for agents."""
    logger.info("🔨 Building default chat graph")
    graph = Graph(State)

    async def chat(state: State) -> dict:
        logger.debug(f"💬 Chat node executing | messages={len(state.messages)}")
        try:
            reply = await engine.chat_text([m.to_openai() for m in state.messages])
            logger.debug(f"✅ Chat node completed | reply_len={len(reply)}")
            return {"messages": [state.messages[-1].reply(reply)]}
        except Exception as exc:
            logger.error(f"❌ Chat node failed: {type(exc).__name__}: {exc}")
            raise

    graph.add_node("chat", chat)
    graph.add_edge(START, "chat")
    graph.add_edge("chat", END)
    logger.info("✅ Chat graph built successfully")
    return graph


def create_app(settings: OmniSettings | None = None) -> FastAPI:
    logger.info("🚀 Creating OmniAI application")

    try:
        settings = settings or get_settings()
        logger.info(f"✅ Settings loaded | engine_managed={settings.engine_managed}")
        settings.validate_security()
        logger.info("✅ Security settings validated")

        logger.info("🔧 Creating ModelEngine")
        engine = ModelEngine.create(
            {
                "model": "served-model",
                "managed": settings.engine_managed,
                "external_base_url": settings.engine_base_url,
                "request_timeout_s": settings.request_timeout_s,
                "retries": settings.engine_retries,
                "breaker_failure_threshold": settings.breaker_failure_threshold,
                "breaker_reset_s": settings.breaker_reset_s,
                "max_concurrent_requests": settings.engine_max_concurrency,
                "supervisor_max_restarts": settings.supervisor_max_restarts,
            }
        )
        logger.info("✅ ModelEngine created")

        logger.info("🔧 Creating InteractionBuffer")
        buffer = InteractionBuffer(settings.database_url, threshold=1000)
        logger.info("✅ InteractionBuffer created")

        logger.info("🔧 Creating ContinuousLearner")
        learner = ContinuousLearner(buffer, LoRATrainer("served-model"), engine=engine)
        buffer.on_threshold = learner.trigger
        logger.info("✅ ContinuousLearner created")

        logger.info("🔧 Building and compiling chat graph")
        chat_handler = build_chat_graph(engine).compile().as_handler()
        logger.info("✅ Chat handler prepared")

        logger.info("🔧 Creating GatewayRouter")
        router = GatewayRouter(
            handler=chat_handler,
            interceptors=[PromptGuard()],
            observers=[buffer],
            settings=settings,
            engine=engine,
            buffer=buffer,
            shutdown_hooks=[engine.stop, buffer.aclose],
        )
        logger.info("✅ GatewayRouter created")

        if router.metrics is not None:
            learner.on_report = lambda report: router.metrics.learning_cycles.labels(
                report["status"]
            ).inc()
            logger.info("✅ Metrics integration enabled")

        logger.info("✅ Application created successfully")
        return router.app
    except Exception as exc:
        logger.error(f"❌ Failed to create application: {type(exc).__name__}: {exc}")
        raise
