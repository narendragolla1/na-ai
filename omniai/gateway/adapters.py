"""Channel adapters: translate native payloads to/from OmniMessage.

Each adapter is a pure codec — no I/O — which keeps the gateway routes thin
and makes the translation logic trivially testable.
"""

from __future__ import annotations

import abc
from typing import Any

from omniai.logging import get_logger
from omniai.protocol import Channel, OmniMessage, Role

logger = get_logger(__name__)


class ChannelAdapter(abc.ABC):
    """Converts a channel's native payload format to/from OmniMessage."""

    channel: Channel

    @abc.abstractmethod
    def to_omni(self, payload: dict[str, Any]) -> OmniMessage:
        """Decode an inbound native payload into the canonical message."""

    @abc.abstractmethod
    def from_omni(self, message: OmniMessage) -> dict[str, Any]:
        """Encode an outbound canonical message into the native format."""


class RestAdapter(ChannelAdapter):
    """Plain JSON: ``{"content": ..., "session_id": ..., "metadata": ...}``."""

    channel = Channel.REST

    def to_omni(self, payload: dict[str, Any]) -> OmniMessage:
        # `.get(key, default)` only falls back when the key is absent; a
        # client sending explicit JSON nulls (`{"content": null}`) must not
        # crash the request, so coalesce None the same way as a missing key.
        content = payload.get("content") or ""
        session_id = payload.get("session_id") or "default"
        metadata = payload.get("metadata") or {}

        logger.debug(
            f"📬 Converting REST payload to OmniMessage | "
            f"session={session_id} | content_len={len(content)} | "
            f"metadata_keys={list(metadata.keys())}"
        )

        try:
            message = OmniMessage(
                channel=self.channel,
                role=Role.USER,
                content=content,
                session_id=session_id,
                metadata=metadata,
            )
            logger.debug(f"✅ Converted to OmniMessage | id={message.id}")
            return message
        except Exception as exc:
            logger.error(f"❌ Failed to convert REST payload: {type(exc).__name__}: {exc}")
            raise

    def from_omni(self, message: OmniMessage) -> dict[str, Any]:
        logger.debug(
            f"📤 Converting OmniMessage to REST | "
            f"id={message.id} | session={message.session_id} | content_len={len(message.content)}"
        )

        try:
            response = {
                "id": message.id,
                "session_id": message.session_id,
                "role": message.role.value,
                "content": message.content,
                "metadata": message.metadata,
            }
            logger.debug("✅ Converted to REST payload")
            return response
        except Exception as exc:
            logger.error(f"❌ Failed to convert to REST: {type(exc).__name__}: {exc}")
            raise


class WebSocketAdapter(RestAdapter):
    """Same JSON shape as REST, delivered over a persistent socket."""

    channel = Channel.WEBSOCKET


class DiscordAdapter(ChannelAdapter):
    """Discord interaction/webhook payloads."""

    channel = Channel.DISCORD
    MAX_CONTENT_LENGTH = 2000

    def to_omni(self, payload: dict[str, Any]) -> OmniMessage:
        content = payload.get("content", "")
        channel_id = str(payload.get("channel_id", "discord"))
        author = payload.get("author", {}) or payload.get("member", {}).get("user", {})

        logger.debug(
            f"📬 Converting Discord payload to OmniMessage | "
            f"channel={channel_id} | author={author.get('username')} | content_len={len(content)}"
        )

        try:
            message = OmniMessage(
                channel=self.channel,
                role=Role.USER,
                content=content,
                session_id=channel_id,
                metadata={
                    "discord_message_id": payload.get("id"),
                    "author_id": author.get("id"),
                    "author_name": author.get("username"),
                    "guild_id": payload.get("guild_id"),
                },
            )
            logger.debug(f"✅ Converted Discord payload to OmniMessage | id={message.id}")
            return message
        except Exception as exc:
            logger.error(f"❌ Failed to convert Discord payload: {type(exc).__name__}: {exc}")
            raise

    def from_omni(self, message: OmniMessage) -> dict[str, Any]:
        content_len = len(message.content)
        truncated = content_len > self.MAX_CONTENT_LENGTH

        logger.debug(
            f"📤 Converting OmniMessage to Discord | "
            f"id={message.id} | content_len={content_len} | truncated={truncated}"
        )

        if truncated:
            logger.warning(
                f"⚠️  Discord content truncated | original_len={content_len} | "
                f"max={self.MAX_CONTENT_LENGTH}"
            )

        try:
            response = {"content": message.content[: self.MAX_CONTENT_LENGTH]}
            logger.debug("✅ Converted to Discord payload")
            return response
        except Exception as exc:
            logger.error(f"❌ Failed to convert to Discord: {type(exc).__name__}: {exc}")
            raise
