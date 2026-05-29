"""Shared theming for the Loadout Editor console output."""

from rich.console import Group
from rich.rule import Rule
from rich.text import Text


ACCENT = "#FF4655"
INK = "#ECE8E1"
MUTED = "#8A93A2"
FAINT = "#3A414E"
GOLD = "#D4AF6A"


def heading(title, subtitle=None):
    head = Text()
    head.append("▌ ", style=f"bold {ACCENT}")
    head.append(title, style=f"bold {INK}")
    if subtitle:
        head.append(f"   {subtitle}", style=MUTED)
    return Group(head, Rule(style=FAINT))


def notice(console, message, kind="info"):
    glyphs = {
        "info": (MUTED, "·"),
        "wait": (GOLD, "◌"),
        "ok": ("#67ED4C", "✓"),
        "warn": (ACCENT, "!"),
    }
    colour, glyph = glyphs.get(kind, glyphs["info"])
    console.print(f"  [{colour}]{glyph}[/]  [{INK}]{message}[/]")
