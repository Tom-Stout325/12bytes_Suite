# MoneyPro custom icon system

## Files
- `templates/icons/airborne-drone.svg`
- `templates/partials/icon.html`
- `static/css/mp_icons.css`

## Add the stylesheet
```django
{% load static %}
<link rel="stylesheet" href="{% static 'css/mp_icons.css' %}">
```

## Use the icon partial
```django
{% include "partials/icon.html" with class="mp-icon-sm" %}
{% include "partials/icon.html" with class="mp-icon-2xl" %}
```

## Size classes
- `mp-icon-xs`
- `mp-icon-sm`
- `mp-icon-md`
- `mp-icon-lg`
- `mp-icon-xl`
- `mp-icon-2xl`
- `mp-icon-3xl`
- `mp-icon-4xl`

## Wrapper classes
- `mp-icon-soft`
- `mp-icon-circle`
- `mp-icon-tile`

Example:
```django
<span class="mp-icon-soft">
    {% include "partials/icon.html" with class="mp-icon-lg" %}
</span>
```
