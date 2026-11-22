"""Helpers for `EnumProperty` items callbacks."""

import functools

_cached_enum_items_funcs = []


def enum_items_cache(func):
    """Decorator for `EnumProperty` items callbacks whose items don't depend on `self` or `context`. Builds the items
    on the first call and returns the same tuple afterwards.

    Use it instead of a static items tuple when the items need something only available after the add-on is
    registered, like custom icons. It caches the items because Blender requires Python to keep a reference to the
    strings returned by an items callback. The items are not cached until the custom icons are registered, and the
    cache is cleared when the add-on is unregistered, because the icon IDs change when it is registered again.
    """

    @functools.wraps(func)
    def items(self=None, context=None):
        try:
            return items._backing
        except AttributeError:
            from ..icons import icon_manager

            result = func(self, context)
            if icon_manager.is_registered:
                items._backing = result
            else:
                # Not cached, the icons would stay as "no icon". Still keep a reference to the strings for Blender.
                items._uncached = result
            return result

    _cached_enum_items_funcs.append(items)
    return items


def post_unregister_classes():
    # After the classes are unregistered, so no callback can cache the soon to be stale icon IDs again
    for items in _cached_enum_items_funcs:
        items.__dict__.pop("_backing", None)
        items.__dict__.pop("_uncached", None)
