from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PipelineEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[PipelineEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._wildcard_handlers: list[EventHandler] = []

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        if event_name == "*":
            self._wildcard_handlers.append(handler)
            return
        self._handlers[event_name].append(handler)

    def publish(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        event = PipelineEvent(name=event_name, payload=payload or {})
        for handler in self._handlers.get(event_name, []):
            handler(event)
        for handler in self._wildcard_handlers:
            handler(event)


class EventCounter:
    def __init__(self) -> None:
        self._counts: dict[str, int] = defaultdict(int)

    def on_event(self, event: PipelineEvent) -> None:
        self._counts[event.name] += 1

    def snapshot(self) -> dict[str, int]:
        return dict(self._counts)


class LoggerEventHandler:
    def __init__(self, logger: Any) -> None:
        self._logger = logger

    def on_event(self, event: PipelineEvent) -> None:
        payload = event.payload or {}
        detail = " ".join(f"{k}={v}" for k, v in payload.items() if v is not None)
        self._logger.debug(f"event:{event.name} {detail}".strip())
