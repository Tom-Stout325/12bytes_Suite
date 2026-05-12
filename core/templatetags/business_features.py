from django import template

register = template.Library()


@register.filter
def has_feature(business, code):
    if not business:
        return False

    return business.has_feature(code)