"""LangGraph-style graph builder and executor.

Nodes are plain Python callables (sync or async) taking the current State and
returning a partial update. Edges are static or conditional (any callable of
the state — lambdas work). Cycles are allowed and bounded by
``max_iterations``, making agent loops (think -> act -> observe) first-class.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from omniai.graph.state import State
from omniai.logging import get_logger
from omniai.telemetry import traced_span

logger = get_logger(__name__)

START = "__start__"
END = "__end__"

NodeFn = Callable[[State], State | dict[str, Any] | None | Awaitable[State | dict[str, Any] | None]]
RouterFn = Callable[[State], str]


class GraphError(Exception):
    pass


class Graph:
    """Mutable builder; call :meth:`compile` to get an executable graph."""

    def __init__(self, state_type: type[State] = State):
        self.state_type = state_type
        self.nodes: dict[str, NodeFn] = {}
        self.edges: dict[str, str] = {}
        self.conditional_edges: dict[str, tuple[RouterFn, dict[str, str] | None]] = {}
        self.entry_point: str | None = None
        logger.debug(f"🔧 Graph initialized | state_type={state_type.__name__}")

    def add_node(self, name: str, fn: NodeFn | None = None) -> Any:
        """Register a node; usable directly or as ``@graph.add_node("name")``."""
        if fn is None:

            def decorator(f: NodeFn) -> NodeFn:
                self.add_node(name, f)
                return f

            return decorator
        if name in (START, END):
            logger.error(f"❌ Cannot add node with reserved name: {name}")
            raise GraphError(f"{name!r} is reserved")
        if name in self.nodes:
            logger.error(f"❌ Node already exists: {name}")
            raise GraphError(f"Node {name!r} already exists")
        self.nodes[name] = fn
        logger.debug(f"✅ Node added | name={name} | total_nodes={len(self.nodes)}")
        return fn

    def set_entry_point(self, name: str) -> None:
        self.entry_point = name
        logger.debug(f"📍 Entry point set | node={name}")

    def add_edge(self, source: str, target: str) -> None:
        if source == START:
            self.set_entry_point(target)
            return
        if source in self.conditional_edges:
            logger.error(f"❌ Node already has conditional edge: {source}")
            raise GraphError(f"Node {source!r} already has a conditional edge")
        self.edges[source] = target
        logger.debug(f"➡️  Edge added | source={source} | target={target}")

    def add_conditional_edges(
        self,
        source: str,
        router: RouterFn,
        path_map: dict[str, str] | None = None,
    ) -> None:
        """Route from ``source`` via ``router(state)``; lambdas are fine.

        The router returns either a node name (or END), or — when ``path_map``
        is given — a key that the map translates to a node name.
        """
        if source in self.edges:
            logger.error(f"❌ Node already has static edge: {source}")
            raise GraphError(f"Node {source!r} already has a static edge")
        self.conditional_edges[source] = (router, path_map)
        logger.debug(
            f"⚡ Conditional edge added | source={source} | path_map_size={len(path_map) if path_map else 0}"
        )

    def compile(self, max_iterations: int = 25) -> CompiledGraph:
        logger.info(f"🔨 Compiling graph | nodes={len(self.nodes)} | edges={len(self.edges)} | conditional={len(self.conditional_edges)}")

        if self.entry_point is None:
            logger.error("❌ Graph compilation failed: No entry point set")
            raise GraphError("No entry point set (use set_entry_point or add_edge(START, ...))")

        referenced = {self.entry_point, *self.edges.values()}
        for _, path_map in self.conditional_edges.values():
            if path_map:
                referenced.update(path_map.values())
        unknown = {r for r in referenced if r != END and r not in self.nodes}
        if unknown:
            logger.error(f"❌ Graph compilation failed: Edges reference unknown nodes: {unknown}")
            raise GraphError(f"Edges reference unknown nodes: {sorted(unknown)}")

        logger.debug(f"✅ Graph structure validated | referenced_nodes={len(referenced)}")
        compiled = CompiledGraph(self, max_iterations=max_iterations)
        logger.info(f"✅ Graph compiled successfully | max_iterations={max_iterations}")
        return compiled


class CompiledGraph:
    """Immutable, executable view of a Graph."""

    def __init__(self, graph: Graph, max_iterations: int = 25):
        self.graph = graph
        self.max_iterations = max_iterations
        logger.debug(f"🔨 CompiledGraph created | nodes={len(graph.nodes)} | max_iterations={max_iterations}")

    async def _run_node(self, name: str, state: State) -> State:
        fn = self.graph.nodes[name]
        logger.debug(f"📞 Running node | node={name}")
        try:
            with traced_span(f"graph.node.{name}", {"node": name}):
                result = fn(state)
                if inspect.isawaitable(result):
                    result = await result
            merged = state.merge(result)
            logger.debug(f"✅ Node completed | node={name} | state_messages={len(merged.messages)}")
            return merged
        except Exception as exc:
            logger.error(f"❌ Node failed | node={name} | error={type(exc).__name__}: {exc}")
            raise

    def _next(self, current: str, state: State) -> str:
        if current in self.graph.conditional_edges:
            router, path_map = self.graph.conditional_edges[current]
            logger.debug(f"⚡ Running conditional router | node={current}")
            try:
                target = router(state)
                if path_map is not None:
                    if target not in path_map:
                        logger.error(f"❌ Router returned unknown path | node={current} | path={target}")
                        raise GraphError(f"Router at {current!r} returned {target!r}, not in path map")
                    target = path_map[target]
                logger.debug(f"➡️  Routed to | target={target}")
                return target
            except Exception as exc:
                logger.error(f"❌ Routing failed | node={current} | error={type(exc).__name__}: {exc}")
                raise
        target = self.graph.edges.get(current, END)
        logger.debug(f"➡️  Next node | current={current} | target={target}")
        return target

    async def ainvoke(self, state: State | dict[str, Any]) -> State:
        """Execute the graph from the entry point until END."""
        if isinstance(state, dict):
            state = self.graph.state_type.model_validate(state)
        assert isinstance(state, State)

        logger.info(f"🚀 Graph execution started | entry_point={self.graph.entry_point} | state_messages={len(state.messages)}")
        current = self.graph.entry_point
        iteration = 0

        try:
            for iteration in range(self.max_iterations):
                if current == END:
                    logger.info(f"✅ Graph execution completed | iterations={iteration} | messages={len(state.messages)}")
                    return state
                if current not in self.graph.nodes:
                    logger.error(f"❌ Unknown node: {current}")
                    raise GraphError(f"Unknown node {current!r}")
                state = await self._run_node(current, state)
                current = self._next(current, state)

            if current == END:
                logger.info(f"✅ Graph execution completed | iterations={self.max_iterations} | messages={len(state.messages)}")
                return state
            logger.error(f"❌ Graph exceeded max iterations | stuck_at={current}")
            raise GraphError(
                f"Graph exceeded max_iterations={self.max_iterations} (stuck at {current!r})"
            )
        except Exception as exc:
            logger.error(f"❌ Graph execution failed after {iteration} iterations: {type(exc).__name__}: {exc}")
            raise

    def invoke(self, state: State | dict[str, Any]) -> State:
        """Synchronous convenience wrapper around :meth:`ainvoke`."""
        import asyncio

        return asyncio.run(self.ainvoke(state))

    def as_handler(self) -> Callable[[Any], Awaitable[Any]]:
        """Adapt this graph to the GatewayRouter handler signature.

        Wraps an inbound OmniMessage in a fresh State, runs the graph, and
        returns the last message in the final state as the reply.
        """
        from omniai.protocol import OmniMessage

        async def handler(message: OmniMessage) -> OmniMessage:
            logger.debug(f"📬 Handler invoked | session={message.session_id} | content_len={len(message.content)}")
            try:
                final = await self.ainvoke(self.graph.state_type(messages=[message]))
                if not final.messages:
                    logger.warning("⚠️  Graph returned empty messages, sending empty reply")
                    return message.reply("")
                last = final.messages[-1]
                reply = message.reply(last.content)
                reply.tool_calls = last.tool_calls
                logger.debug(f"✅ Handler returned reply | reply_len={len(reply.content)} | tool_calls={len(reply.tool_calls)}")
                return reply
            except Exception as exc:
                logger.error(f"❌ Handler failed: {type(exc).__name__}: {exc}")
                raise

        return handler
