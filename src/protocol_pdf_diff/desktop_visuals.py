"""Small Tk drawing primitives used by the desktop comparison studio."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable


def rounded_polygon_points(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
) -> tuple[float, ...]:
    """Return smooth-polygon control points for a rounded rectangle."""

    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    return (
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    )


def draw_rounded_rectangle(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    radius: float,
    fill: str,
    outline: str = "",
    width: int = 1,
    dash: tuple[int, int] | None = None,
    tags: str | tuple[str, ...] = (),
) -> int:
    """Draw one anti-aliased-looking rounded surface using a smooth polygon."""

    return int(
        canvas.create_polygon(
            rounded_polygon_points(x1, y1, x2, y2, radius),
            smooth=True,
            splinesteps=24,
            fill=fill,
            outline=outline,
            width=width,
            dash=dash,
            tags=tags,
        )
    )


class GlassPanel(tk.Canvas):
    """Rounded solid-color approximation of a glass panel without GUI dependencies."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        background: str,
        surface: str,
        border: str,
        radius: int = 28,
        inset: int = 2,
        height: int = 430,
    ) -> None:
        super().__init__(
            parent,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            width=420,
            height=height,
        )
        self.surface = surface
        self.border = border
        self.radius = radius
        self.inset = inset
        self.content = tk.Frame(self, background=surface, borderwidth=0)
        self._content_window = self.create_window((0, 0), window=self.content, anchor="nw")
        self.bind("<Configure>", self._redraw, add="+")

    def _redraw(self, event: tk.Event) -> None:
        width = max(1, int(event.width))
        height = max(1, int(event.height))
        self.delete("panel-surface")
        draw_rounded_rectangle(
            self,
            1,
            1,
            width - 1,
            height - 1,
            radius=self.radius,
            fill=self.surface,
            outline=self.border,
            width=1,
            tags="panel-surface",
        )
        self.tag_lower("panel-surface")
        pad = max(self.inset, self.radius // 3)
        self.coords(self._content_window, pad, pad)
        self.itemconfigure(
            self._content_window,
            width=max(1, width - 2 * pad),
            height=max(1, height - 2 * pad),
        )


class DropZone(tk.Canvas):
    """Large rounded PDF selection target with an honest click-only interaction."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        background: str,
        surface: str,
        border: str,
        accent: str,
        accent_fold: str,
        title: str,
        font_family: str,
        command: Callable[[], None],
        height: int = 276,
    ) -> None:
        super().__init__(
            parent,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            height=height,
            cursor="hand2",
            takefocus=1,
        )
        self.surface = surface
        self.border = border
        self.accent = accent
        self.accent_fold = accent_fold
        self.title = title
        self.font_family = font_family
        self.command = command
        self.display_name = ""
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Button-1>", self._activate, add="+")
        self.bind("<Return>", self._activate, add="+")
        self.bind("<space>", self._activate, add="+")

    def cget(self, key: str) -> object:
        """Expose button-like metadata to the existing packaging smoke contract."""

        if key == "text":
            return "选择 PDF"
        if key == "command":
            return self.command
        return super().cget(key)

    def set_display_name(self, name: str) -> None:
        self.display_name = name
        if self.winfo_width() > 1:
            self._paint(self.winfo_width(), self.winfo_height())

    def _activate(self, _event: tk.Event) -> None:
        if str(self.cget("state")) != "disabled":
            self.command()

    def _redraw(self, event: tk.Event) -> None:
        self._paint(max(1, int(event.width)), max(1, int(event.height)))

    def _paint(self, width: int, height: int) -> None:
        self.delete("all")
        draw_rounded_rectangle(
            self,
            1,
            1,
            width - 1,
            height - 1,
            radius=22,
            fill=self.surface,
            outline=self.border,
            width=1,
            dash=(5, 5),
        )
        glyph_width = 82
        glyph_height = 102
        gx = width / 2 - glyph_width / 2
        gy = max(24, height / 2 - 82)
        draw_rounded_rectangle(
            self,
            gx,
            gy,
            gx + glyph_width,
            gy + glyph_height,
            radius=20,
            fill=self.accent,
        )
        self.create_polygon(
            gx + glyph_width - 25,
            gy,
            gx + glyph_width,
            gy + 25,
            gx + glyph_width - 25,
            gy + 25,
            fill=self.accent_fold,
            outline="",
        )
        for offset, line_width in ((0, 34), (12, 30), (24, 24)):
            self.create_line(
                width / 2 - 17,
                gy + 46 + offset,
                width / 2 - 17 + line_width,
                gy + 46 + offset,
                fill="#F8F6FF",
                width=3,
            )
        label_y = gy + glyph_height + 24
        self.create_text(
            width / 2,
            label_y,
            text=self.display_name or self.title,
            fill="#F7F5FF",
            font=(self.font_family, 15, "bold"),
            width=max(120, width - 52),
        )
        self.create_text(
            width / 2,
            label_y + 28,
            text="点击选择文件",
            fill="#9F9BB1",
            font=(self.font_family, 11),
        )


class RoundedButton(tk.Canvas):
    """Keyboard-accessible rounded Canvas button with the Tk button surface API."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str,
        command: Callable[[], None],
        font_family: str,
        background: str,
        fill: str,
        foreground: str,
        border: str = "",
        active_fill: str | None = None,
        disabled_fill: str | None = None,
        disabled_foreground: str = "#79758D",
        width: int = 126,
        height: int = 46,
        radius: int = 15,
        bold: bool = True,
    ) -> None:
        super().__init__(
            parent,
            width=width,
            height=height,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
            takefocus=1,
        )
        self.text = text
        self.command = command
        self.font_family = font_family
        self.fill = fill
        self.foreground = foreground
        self.border = border
        self.active_fill = active_fill or fill
        self.disabled_fill = disabled_fill or fill
        self.disabled_foreground = disabled_foreground
        self.radius = radius
        self.state = "normal"
        self.bold = bold
        self.bind("<Configure>", lambda event: self._paint(), add="+")
        self.bind("<Button-1>", lambda event: self.invoke(), add="+")
        self.bind("<Return>", lambda event: self.invoke(), add="+")
        self.bind("<space>", lambda event: self.invoke(), add="+")
        self.bind("<Enter>", lambda event: self._paint(active=True), add="+")
        self.bind("<Leave>", lambda event: self._paint(), add="+")

    def cget(self, key: str) -> object:
        if key == "text":
            return self.text
        if key == "command":
            return self.command
        if key == "state":
            return self.state
        return super().cget(key)

    def configure(self, cnf: object = None, **kwargs: object) -> object:
        if cnf is not None:
            return super().configure(cnf, **kwargs)
        for key in ("text", "state"):
            if key in kwargs:
                setattr(self, key, str(kwargs.pop(key)))
        result = super().configure(**kwargs) if kwargs else None
        self._paint()
        return result

    config = configure

    def invoke(self) -> object | None:
        if self.state == "disabled":
            return None
        return self.command()

    def _paint(self, *, active: bool = False) -> None:
        self.delete("all")
        disabled = self.state == "disabled"
        fill = self.disabled_fill if disabled else self.active_fill if active else self.fill
        foreground = self.disabled_foreground if disabled else self.foreground
        draw_rounded_rectangle(
            self,
            1,
            1,
            max(2, self.winfo_width() - 1),
            max(2, self.winfo_height() - 1),
            radius=self.radius,
            fill=fill,
            outline=self.border,
        )
        self.create_text(
            self.winfo_width() / 2,
            self.winfo_height() / 2,
            text=self.text,
            fill=foreground,
            font=(self.font_family, 12, "bold" if self.bold else "normal"),
        )


class PillRadio(tk.Canvas):
    """A compact rounded page-mode choice without native radio indicator noise."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str,
        variable: tk.StringVar,
        value: str,
        command: Callable[[], None],
        font_family: str,
        background: str,
        fill: str,
        selected_fill: str,
        border: str,
        foreground: str,
        selected_border: str,
        width: int = 86,
        height: int = 34,
    ) -> None:
        super().__init__(
            parent,
            width=width,
            height=height,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
            takefocus=1,
        )
        self.text = text
        self.variable = variable
        self.value = value
        self.command = command
        self.font_family = font_family
        self.fill = fill
        self.selected_fill = selected_fill
        self.border = border
        self.foreground = foreground
        self.selected_border = selected_border
        self.state = "normal"
        self.variable.trace_add("write", lambda *_args: self._paint())
        self.bind("<Configure>", lambda event: self._paint(), add="+")
        self.bind("<Button-1>", lambda event: self.invoke(), add="+")
        self.bind("<Return>", lambda event: self.invoke(), add="+")
        self.bind("<space>", lambda event: self.invoke(), add="+")

    def cget(self, key: str) -> object:
        if key == "text":
            return self.text
        if key == "state":
            return self.state
        return super().cget(key)

    def configure(self, cnf: object = None, **kwargs: object) -> object:
        if cnf is not None:
            return super().configure(cnf, **kwargs)
        if "state" in kwargs:
            self.state = str(kwargs.pop("state"))
        result = super().configure(**kwargs) if kwargs else None
        self._paint()
        return result

    config = configure

    def invoke(self) -> None:
        if self.state == "disabled":
            return
        self.variable.set(self.value)
        self.command()

    def _paint(self) -> None:
        self.delete("all")
        selected = self.variable.get() == self.value
        fill = self.selected_fill if selected else self.fill
        outline = self.selected_border if selected else self.border
        foreground = self.foreground if self.state != "disabled" else "#79758D"
        draw_rounded_rectangle(
            self,
            1,
            1,
            max(2, self.winfo_width() - 1),
            max(2, self.winfo_height() - 1),
            radius=16,
            fill=fill,
            outline=outline,
        )
        self.create_text(
            self.winfo_width() / 2,
            self.winfo_height() / 2,
            text=self.text,
            fill=foreground,
            font=(self.font_family, 11),
        )
