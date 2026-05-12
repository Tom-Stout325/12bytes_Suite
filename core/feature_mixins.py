from django.http import Http404


class FeatureRequiredMixin:
    required_feature = None

    def dispatch(self, request, *args, **kwargs):
        business = getattr(request, "business", None)

        if not business:
            raise Http404()

        if not business.has_feature(self.required_feature):
            raise Http404()

        return super().dispatch(request, *args, **kwargs)