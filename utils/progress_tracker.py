"""Single compact progress indicator for the annotation pipeline."""

from __future__ import annotations

import chainlit as cl

from utils.ui_components import progress_panel_html


class PipelineProgress:
    """One updatable status message — removed when the pipeline finishes."""

    def __init__(self) -> None:
        self._message: cl.Message | None = None
        self._label = "Starting…"
        self._detail = ""

    async def start(self, label: str = "Starting annotation…", detail: str = "") -> None:
        self._label = label
        self._detail = detail
        self._message = cl.Message(content=progress_panel_html(label, detail))
        await self._message.send()

    async def update(self, label: str, detail: str = "") -> None:
        self._label = label
        self._detail = detail
        if self._message is None:
            await self.start(label, detail)
            return
        self._message.content = progress_panel_html(label, detail)
        await self._message.update()

    async def finish(self) -> None:
        if self._message is not None:
            await self._message.remove()
            self._message = None
