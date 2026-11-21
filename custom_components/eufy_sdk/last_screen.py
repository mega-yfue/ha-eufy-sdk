"""Consume explicitly advertised last-screen views, retaining legacy primary support."""

from __future__ import annotations

from typing import Any


def last_screen_views(device: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only supported views; older bridge inventories expose one primary."""
    views = [view for view in device.get("views", []) if view.get("lastScreen")]
    if device.get("lastScreen") and not any(view["view"] == 0 for view in views):
        views.insert(0, {"view": 0, "lastScreen": device["lastScreen"]})
    return views
