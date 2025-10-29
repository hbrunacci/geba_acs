from django import template

register = template.Library()


@register.filter
def get_item(obj, key):
    """Safely access dictionary or object attributes within templates."""
    if obj is None or key is None:
        return ""
    if isinstance(obj, (list, tuple)):
        try:
            index = int(key)
        except (ValueError, TypeError):
            return ""
        try:
            return obj[index]
        except IndexError:
            return ""
    if isinstance(obj, dict):
        return obj.get(key, "")

    return getattr(obj, key, "")
