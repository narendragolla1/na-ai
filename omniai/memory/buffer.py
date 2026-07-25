"""Interaction buffer: async, database-agnostic logging of gateway traffic.

Backed by SQLModel over SQLAlchemy's async engine, so the storage backend is
selected purely by URL — ``postgresql+asyncpg://`` in production,
``sqlite+aiosqlite://`` for zero-config dev, or any other async dialect.
Plain file paths are accepted for backward compatibility and treated as
SQLite databases.

Designed to be attached as a GatewayRouter observer — it receives every
inbound user message, tool output, and LLM response without blocking the
event loop.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from omniai.logging import DatabaseError, get_logger
from omniai.memory.models import Interaction
from omniai.protocol import OmniMessage

logger = get_logger(__name__)


def _to_url(target: str | Path) -> str:
    target = str(target)
    if "://" in target:
        return target
    return f"sqlite+aiosqlite:///{target}"


class InteractionBuffer:
    """Async interaction log with a training-trigger threshold."""

    def __init__(
        self,
        database_url: str | Path = "sqlite+aiosqlite:///interactions.db",
        threshold: int | None = None,
        on_threshold: Callable[[], Awaitable[Any] | Any] | None = None,
    ):
        self.database_url = _to_url(database_url)
        self.threshold = threshold
        self.on_threshold = on_threshold
        self._engine: AsyncEngine | None = None
        self._ready_lock = asyncio.Lock()
        self._threshold_fired_at: int | None = None
        # Incrementally maintained so the threshold check never needs a
        # COUNT(*) round-trip per logged message. Seeded from the DB once;
        # slight drift on upserts is harmless for threshold purposes.
        self._approx_count: int = 0
        logger.info(
            f"🔧 InteractionBuffer initialized | database={self.database_url} | "
            f"threshold={threshold} | has_callback={on_threshold is not None}"
        )

    async def _ensure_ready(self) -> AsyncEngine:
        if self._engine is None:
            async with self._ready_lock:
                if self._engine is None:
                    logger.info(f"🔌 Connecting to database | url={self.database_url}")
                    try:
                        engine = create_async_engine(self.database_url)
                        async with engine.begin() as conn:
                            await conn.run_sync(SQLModel.metadata.create_all)
                        self._engine = engine
                        logger.info(f"✅ Database connected and initialized")
                    except Exception as exc:
                        logger.error(f"❌ Database connection failed: {type(exc).__name__}: {exc}")
                        raise DatabaseError(
                            "Failed to connect to database",
                            context=f"URL: {self.database_url}",
                            suggestion="Check database URL and credentials",
                        ) from exc
        if self._threshold_fired_at is None:
            try:
                self._approx_count = await self._count()
                self._threshold_fired_at = self._approx_count
                logger.debug(f"📊 Interaction count initialized | count={self._approx_count}")
            except Exception as exc:
                logger.error(f"❌ Failed to count interactions: {type(exc).__name__}: {exc}")
                raise
        return self._engine

    async def _count(self) -> int:
        from sqlalchemy import func

        try:
            async with AsyncSession(self._engine) as session:
                result = await session.exec(select(func.count()).select_from(Interaction))
                return int(result.one())
        except Exception as exc:
            logger.error(f"❌ Failed to count interactions: {type(exc).__name__}: {exc}")
            raise

    @staticmethod
    def _to_row(message: OmniMessage) -> Interaction:
        # Store naive UTC: portable across TIMESTAMP flavors (asyncpg rejects
        # tz-aware values for TIMESTAMP WITHOUT TIME ZONE).
        created = message.created_at
        if created.tzinfo is not None:
            created = created.astimezone(UTC).replace(tzinfo=None)
        return Interaction(
            id=message.id,
            session_id=message.session_id,
            channel=message.channel.value,
            role=message.role.value,
            content=message.content,
            tool_calls=json.dumps([tc.model_dump() for tc in message.tool_calls]),
            metadata_json=json.dumps(message.metadata, default=str),
            created_at=created,
        )

    async def log(self, message: OmniMessage) -> None:
        """Persist a message; fires ``on_threshold`` when the bar is crossed."""
        logger.debug(
            f"💾 Logging interaction | id={message.id} | session={message.session_id} | role={message.role.value}"
        )
        try:
            await self._ensure_ready()
            async with AsyncSession(self._engine) as session:
                await session.merge(self._to_row(message))
                await session.commit()
            logger.debug(f"✅ Interaction logged | id={message.id}")
        except Exception as exc:
            logger.error(f"❌ Failed to log interaction: {type(exc).__name__}: {exc}")
            raise

        self._approx_count += 1
        if self.threshold is None or self.on_threshold is None:
            return

        if self._approx_count - (self._threshold_fired_at or 0) >= self.threshold:
            logger.info(
                f"🎯 Interaction threshold reached | threshold={self.threshold} | count={self._approx_count}"
            )
            self._threshold_fired_at = self._approx_count
            try:
                result = self.on_threshold()
                if asyncio.iscoroutine(result):
                    await result
                logger.info(f"✅ Threshold callback completed")
            except Exception as exc:
                logger.error(f"❌ Threshold callback failed: {type(exc).__name__}: {exc}")
                raise

    async def count(self) -> int:
        try:
            await self._ensure_ready()
            count = await self._count()
            logger.debug(f"📊 Interaction count retrieved | count={count}")
            return count
        except Exception as exc:
            logger.error(f"❌ Failed to get interaction count: {type(exc).__name__}: {exc}")
            raise

    async def fetch(
        self,
        session_id: str | None = None,
        limit: int | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Rows as plain dicts, oldest first (keys match the legacy schema).

        ``since`` filters to rows created strictly after the given (naive
        UTC) timestamp — the incremental-training high-water mark.
        """
        logger.debug(
            f"🔍 Fetching interactions | session={session_id} | limit={limit} | since={since is not None}"
        )
        try:
            await self._ensure_ready()
            query = select(Interaction)
            if session_id is not None:
                query = query.where(Interaction.session_id == session_id)
            if since is not None:
                if since.tzinfo is not None:
                    since = since.astimezone(UTC).replace(tzinfo=None)
                query = query.where(Interaction.created_at > since)
            query = query.order_by(Interaction.created_at, Interaction.id)
            if limit is not None:
                query = query.limit(limit)
            async with AsyncSession(self._engine) as session:
                rows = (await session.exec(query)).all()
            logger.debug(f"✅ Fetched interactions | count={len(rows)}")
            return [
                {
                    "id": r.id,
                    "session_id": r.session_id,
                    "channel": r.channel,
                    "role": r.role,
                    "content": r.content,
                    "tool_calls": r.tool_calls,
                    "metadata": r.metadata_json,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        except Exception as exc:
            logger.error(f"❌ Failed to fetch interactions: {type(exc).__name__}: {exc}")
            raise

    async def aclose(self) -> None:
        logger.info("🛑 Closing InteractionBuffer")
        if self._engine is not None:
            try:
                await self._engine.dispose()
                self._engine = None
                logger.info("✅ Database connection closed")
            except Exception as exc:
                logger.error(f"❌ Error closing database: {type(exc).__name__}: {exc}")

    def close(self) -> None:
        """Sync-friendly close; schedules disposal if a loop is running."""
        if self._engine is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
        else:
            loop.create_task(self.aclose())

    # Allows: GatewayRouter(observers=[buffer])
    async def __call__(self, message: OmniMessage) -> None:
        await self.log(message)
