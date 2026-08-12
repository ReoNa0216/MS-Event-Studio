"""MS Event Studio visual system and packaged brand assets."""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass
from importlib import resources
from typing import Any


@dataclass(frozen=True, slots=True)
class Palette:
    # Shared LMA Studio family neutrals. MS Event Studio keeps its own
    # cyan/teal scientific accent and peak-oriented visual identity.
    canvas: str = "#F6F7F9"
    surface: str = "#FFFFFF"
    surface_alt: str = "#EEF1F4"
    navy: str = "#111827"
    navy_soft: str = "#1F2937"
    cyan: str = "#0E8AA6"
    cyan_hover: str = "#0B6F86"
    mint: str = "#2A7D67"
    text: str = "#1B1F27"
    muted: str = "#667085"
    border: str = "#D7DCE3"
    success: str = "#12805C"
    success_hover: str = "#0E684C"
    warning: str = "#A96100"
    danger: str = "#C2382B"
    danger_hover: str = "#A62F25"
    focus: str = "#9DDDEA"
    trace: str = "#0E7490"
    plot: str = "#FBFCFD"
    grid: str = "#E7EAEE"


PALETTE = Palette()


def font_family() -> str:
    if sys.platform == "win32":
        return "Microsoft YaHei UI"
    if sys.platform == "darwin":
        return "PingFang SC"
    return "DejaVu Sans"


def icon_photo(master: Any, size: int):
    """Load a package icon without relying on a source-tree filesystem path."""

    import tkinter as tk

    if size not in {32, 64, 128, 256}:
        raise ValueError(f"unsupported packaged icon size: {size}")
    payload = (
        resources.files("ms_event_studio")
        .joinpath(f"assets/app_icon_{size}.png")
        .read_bytes()
    )
    encoded = base64.b64encode(payload).decode("ascii")
    return tk.PhotoImage(master=master, data=encoded, format="png")


def configure_theme(root: Any) -> Any:
    """Apply one cross-platform ttk theme; return the configured Style."""

    from tkinter import font, ttk

    palette = PALETTE
    family = font_family()
    for name, size, weight in (
        ("TkDefaultFont", 10, "normal"),
        ("TkTextFont", 10, "normal"),
        ("TkMenuFont", 10, "normal"),
        ("TkHeadingFont", 10, "bold"),
    ):
        try:
            named = font.nametofont(name)
            named.configure(family=family, size=size, weight=weight)
        except Exception:
            continue

    root.configure(background=palette.canvas)
    root.option_add("*Font", (family, 10))
    root.option_add("*tearOff", False)
    root.option_add("*Text.background", palette.surface)
    root.option_add("*Text.foreground", palette.text)
    root.option_add("*Text.insertBackground", palette.text)
    root.option_add("*Text.selectBackground", palette.focus)
    root.option_add("*Text.selectForeground", palette.navy)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=palette.canvas, foreground=palette.text)
    style.configure("TFrame", background=palette.canvas)
    style.configure("Page.TFrame", background=palette.canvas)
    style.configure("Surface.TFrame", background=palette.surface)
    style.configure(
        "Card.TFrame",
        background=palette.surface,
        bordercolor=palette.border,
        lightcolor=palette.border,
        darkcolor=palette.border,
        borderwidth=1,
        relief="solid",
    )
    style.configure("Hero.TFrame", background=palette.navy)
    style.configure("HeroSoft.TFrame", background=palette.navy_soft)
    style.configure("Status.TFrame", background=palette.navy)

    style.configure("TLabel", background=palette.canvas, foreground=palette.text)
    style.configure("Surface.TLabel", background=palette.surface, foreground=palette.text)
    style.configure("Muted.TLabel", background=palette.canvas, foreground=palette.muted)
    style.configure("SurfaceMuted.TLabel", background=palette.surface, foreground=palette.muted)
    style.configure(
        "Eyebrow.TLabel",
        background=palette.surface,
        foreground=palette.cyan_hover,
        font=(family, 9, "bold"),
    )
    style.configure(
        "Title.TLabel",
        background=palette.canvas,
        foreground=palette.navy,
        font=(family, 24, "bold"),
    )
    style.configure(
        "SurfaceTitle.TLabel",
        background=palette.surface,
        foreground=palette.navy,
        font=(family, 18, "bold"),
    )
    style.configure(
        "Section.TLabel",
        background=palette.surface,
        foreground=palette.navy,
        font=(family, 12, "bold"),
    )
    style.configure(
        "HeroTitle.TLabel",
        background=palette.navy,
        foreground="#FFFFFF",
        font=(family, 22, "bold"),
    )
    style.configure(
        "HeroSubtitle.TLabel",
        background=palette.navy,
        foreground="#BFD0E6",
        font=(family, 11),
    )
    style.configure(
        "HeroBody.TLabel",
        background=palette.navy,
        foreground="#E7F0FA",
        font=(family, 10),
    )
    style.configure(
        "Status.TLabel",
        background=palette.navy,
        foreground="#DDEAF7",
        font=(family, 9),
    )
    style.configure(
        "SuccessPill.TLabel",
        background="#DDF7ED",
        foreground="#0E684C",
        font=(family, 9, "bold"),
        padding=(9, 4),
    )
    style.configure(
        "InfoPill.TLabel",
        background="#DFF5FB",
        foreground="#087D9F",
        font=(family, 9, "bold"),
        padding=(9, 4),
    )

    style.configure(
        "TButton",
        font=(family, 10, "bold"),
        padding=(13, 8),
        borderwidth=1,
        relief="flat",
        focuscolor=palette.focus,
    )
    style.configure(
        "Primary.TButton",
        background=palette.cyan,
        foreground="#FFFFFF",
        bordercolor=palette.cyan,
    )
    style.map(
        "Primary.TButton",
        background=[("pressed", palette.cyan_hover), ("active", "#159AB7")],
        bordercolor=[("focus", palette.focus)],
        foreground=[("disabled", "#69788B")],
    )
    style.configure(
        "Secondary.TButton",
        background=palette.surface,
        foreground=palette.navy,
        bordercolor=palette.border,
    )
    style.map(
        "Secondary.TButton",
        background=[("pressed", palette.surface_alt), ("active", "#F7FAFC")],
        bordercolor=[("focus", palette.cyan), ("active", "#B9C9D9")],
    )
    style.configure(
        "Ghost.TButton",
        background=palette.navy,
        foreground="#EAF5FF",
        bordercolor="#36506F",
    )
    style.map(
        "Ghost.TButton",
        background=[("pressed", "#203A5D"), ("active", palette.navy_soft)],
        bordercolor=[("active", palette.cyan)],
    )
    style.configure(
        "Success.TButton",
        background=palette.success,
        foreground="#FFFFFF",
        bordercolor=palette.success,
    )
    style.map("Success.TButton", background=[("active", palette.success_hover)])
    style.configure(
        "Danger.TButton",
        background=palette.danger,
        foreground="#FFFFFF",
        bordercolor=palette.danger,
    )
    style.map("Danger.TButton", background=[("active", palette.danger_hover)])
    style.configure(
        "Warning.TButton",
        background="#F6E8D1",
        foreground="#7A4800",
        bordercolor="#E7C792",
    )
    style.map("Warning.TButton", background=[("active", "#EFDAB8")])
    style.configure(
        "Toolbar.TButton",
        background=palette.surface_alt,
        foreground=palette.navy,
        bordercolor=palette.border,
        padding=(9, 6),
        font=(family, 9, "bold"),
    )
    style.map("Toolbar.TButton", background=[("active", "#DDE8F2")])

    style.configure(
        "TEntry",
        fieldbackground=palette.surface,
        foreground=palette.text,
        bordercolor=palette.border,
        lightcolor=palette.border,
        darkcolor=palette.border,
        insertcolor=palette.text,
        padding=(8, 7),
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", palette.cyan), ("invalid", palette.danger)],
        lightcolor=[("focus", palette.cyan)],
        darkcolor=[("focus", palette.cyan)],
    )
    style.configure(
        "TCombobox",
        fieldbackground=palette.surface,
        background=palette.surface_alt,
        foreground=palette.text,
        arrowcolor=palette.navy,
        bordercolor=palette.border,
        padding=(7, 5),
    )
    style.map("TCombobox", bordercolor=[("focus", palette.cyan)])
    style.configure("TCheckbutton", background=palette.surface, foreground=palette.text)
    style.map("TCheckbutton", background=[("active", palette.surface)])
    style.configure(
        "TLabelframe",
        background=palette.surface,
        bordercolor=palette.border,
        lightcolor=palette.border,
        darkcolor=palette.border,
        borderwidth=1,
        relief="solid",
    )
    style.configure(
        "TLabelframe.Label",
        background=palette.surface,
        foreground=palette.navy,
        font=(family, 10, "bold"),
    )
    style.configure("TPanedwindow", background=palette.canvas, sashwidth=8)
    style.configure(
        "TNotebook",
        background=palette.surface,
        bordercolor=palette.border,
        lightcolor=palette.border,
        darkcolor=palette.border,
        borderwidth=1,
    )
    style.configure(
        "TNotebook.Tab",
        background=palette.surface_alt,
        foreground=palette.muted,
        padding=(13, 7),
        font=(family, 9, "bold"),
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", palette.surface), ("active", "#DDE8F2")],
        foreground=[("selected", palette.navy), ("active", palette.navy)],
    )
    style.configure(
        "Accent.Horizontal.TProgressbar",
        troughcolor=palette.surface_alt,
        background=palette.cyan,
        bordercolor=palette.surface_alt,
        lightcolor=palette.cyan,
        darkcolor=palette.cyan,
        thickness=9,
    )
    style.configure("TSeparator", background=palette.border)
    return style
