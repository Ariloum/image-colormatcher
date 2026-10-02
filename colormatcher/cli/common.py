"""Общие помощники CLI."""

from __future__ import annotations

import typer

from ..core.filters import FilterRegistry

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}


def parse_sets(sets: list[str]) -> dict[str, float | bool]:
    """`cb.shadows.cr=10`, `cb.preserve_luminosity=false` -> словарь с проверкой ключей."""
    out: dict[str, float | bool] = {}
    for item in sets:
        key, sep, raw = item.partition("=")
        if not sep:
            raise typer.BadParameter(f"ожидается ключ=значение: {item!r}")
        key = key.strip()
        if key != "opacity":
            try:
                FilterRegistry.resolve_key(key)
            except KeyError as exc:
                raise typer.BadParameter(str(exc)) from None
        low = raw.strip().lower()
        if low in _TRUE or low in _FALSE:
            out[key] = low in _TRUE
        else:
            try:
                out[key] = float(low)
            except ValueError:
                raise typer.BadParameter(f"{key}: ожидается число или true/false, получено {raw!r}") from None
    return out



def parse_limits(items: list[str]) -> dict[str, float]:
    """`bc.brightness=20`, `cb=40` -> словарь пределов решателя."""
    out = {}
    for it in items:
        k, sep, v = it.partition("=")
        if not sep:
            raise typer.BadParameter(f"--limit ожидает ключ=значение: {it!r}")
        out[k.strip()] = float(v)
    return out
