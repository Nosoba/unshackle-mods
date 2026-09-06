"""Tests for Rich gradient text helpers."""

from __future__ import annotations

import re
from io import StringIO

import pytest

from rich.color import Color
from rich.text import Text

from unshackle.core.console import ComfyConsole, ComfyLogRenderer, gradient_text


def _plain(value: str) -> str:
    """Strip Rich ANSI SGR codes so a contiguous string assertion is reliable."""
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def test_gradient_text_applies_endpoint_colours() -> None:
    text = gradient_text("Unshackle", colors=("#ff0000", "#0000ff"), style="bold")

    assert text.plain == "Unshackle"
    assert len(text.spans) == len("Unshackle")
    first_span = next(s for s in text.spans if s.start == 0)
    last_span = next(s for s in text.spans if s.start == len("Unshackle") - 1)
    # The gradient blends through intermediate colours, so only the endpoints are exact.
    assert len({(span.start, span.end) for span in text.spans}) == len("Unshackle")
    assert first_span.style.color.triplet == Color.parse("#ff0000").triplet
    assert last_span.style.color.triplet == Color.parse("#0000ff").triplet


def test_gradient_text_preserves_whitespace_without_colour_stop() -> None:
    text = gradient_text("A B", colors=("#ff0000", "#0000ff"))

    assert text.plain == "A B"
    # Rich only emits spans for styled characters; the space stays unstyled.
    assert not any(span.start <= 1 < span.end for span in text.spans)


def test_gradient_text_renders_on_standard_colour_terminal() -> None:
    output = StringIO()
    terminal = ComfyConsole(file=output, force_terminal=True, color_system="standard")

    terminal.print(gradient_text("Gradient", colors=("#ff0000", "#0000ff")))

    assert "Gradient" in _plain(output.getvalue())


def test_gradient_text_requires_at_least_one_colour() -> None:
    with pytest.raises(ValueError, match="At least one gradient color"):
        gradient_text("Gradient", colors=())


def test_log_renderer_gradients_plain_message() -> None:
    output = StringIO()
    terminal = ComfyConsole(file=output, force_terminal=True, color_system="standard")
    renderer = ComfyLogRenderer(show_time=False, show_path=False)

    table = renderer(terminal, ("Loaded 26 services",))
    terminal.print(table)

    assert "Loaded 26 services" in _plain(output.getvalue())


def test_log_renderer_preserves_styled_accent_over_gradient() -> None:
    # A Text with a green accent span should keep that accent over the gradient base.
    message = Text("+ Login successful")
    message.stylize("green", 0, 1)

    output = StringIO()
    terminal = ComfyConsole(file=output, force_terminal=True, color_system="standard")
    renderer = ComfyLogRenderer(show_time=False, show_path=False)

    table = renderer(terminal, (message,))
    terminal.print(table)

    assert "+ Login successful" in _plain(output.getvalue())


def test_gradient_log_message_rebuilds_text_keeping_spans() -> None:
    message = Text("Login ok")
    message.stylize("green", 0, 5)

    transformed = _gradient_log_message_for_test(message)

    assert isinstance(transformed, Text)
    assert transformed.plain == "Login ok"
    # original semantic span is reapplied over the gradient base
    assert any(span.start == 0 and span.end == 5 for span in transformed.spans)


def _gradient_log_message_for_test(renderable: object) -> object:
    from unshackle.core.console import _gradient_log_message

    return _gradient_log_message(renderable)
