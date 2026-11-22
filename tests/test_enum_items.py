"""Tests for the `EnumProperty` items callbacks cache (shared/enum_items.py)."""

from .. import icons
from ..shared import enum_items
from ..shared.enum_items import enum_items_cache


def _make_counted_items():
    calls = []

    @enum_items_cache
    def items(self, context):
        calls.append(1)
        return (("A", "A", "", 1),)

    return items, calls


def test_enum_items_cache_rebuilds_after_unregister():
    items, calls = _make_counted_items()

    first = items(None, None)
    assert items(None, None) is first
    assert len(calls) == 1

    # Like disabling and enabling the add-on again: the icon IDs change, so the items must be built again
    enum_items.post_unregister_classes()

    items(None, None)
    assert len(calls) == 2


def test_enum_items_cache_not_cached_before_icons_registered(monkeypatch):
    items, calls = _make_counted_items()
    monkeypatch.setattr(icons.IconManager, "is_registered", property(lambda self: False))

    items(None, None)
    items(None, None)

    assert len(calls) == 2
