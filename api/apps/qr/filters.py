from django_filters import rest_framework as filters

from apps.qr.models import QrCode


class QrFilter(filters.FilterSet):
    """Filtres de liste. `archived`/`deleted` sont explicites : par défaut on ne montre que le vivant."""

    type = filters.CharFilter(field_name="type_id")
    kind = filters.ChoiceFilter(choices=[("static", "Statique"), ("dynamic", "Dynamique")])
    active = filters.BooleanFilter(field_name="is_active")
    archived = filters.BooleanFilter(method="filter_archived")
    deleted = filters.BooleanFilter(method="filter_deleted")
    search = filters.CharFilter(method="filter_search", label="recherche")

    class Meta:
        model = QrCode
        fields = ["type", "kind", "active"]

    def filter_archived(self, queryset, name, value):
        return queryset.filter(archived_at__isnull=False) if value else queryset.filter(archived_at__isnull=True)

    def filter_deleted(self, queryset, name, value):
        return queryset.filter(deleted_at__isnull=False) if value else queryset.filter(deleted_at__isnull=True)

    def filter_search(self, queryset, name, value):
        value = (value or "").strip()
        if not value:
            return queryset
        from django.db.models import Q

        return queryset.filter(Q(label__icontains=value) | Q(slug__iexact=value) | Q(notes__icontains=value))
