"""Pagination par curseur : un `offset` profond sur 5 M de QR coûte une séquence complète."""

from __future__ import annotations

from rest_framework.pagination import CursorPagination


class CursorById(CursorPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
    # `ordering="-created_at"` seul serait ambiguë (deux créations à la même seconde) : on casse
    # l'égalité par l'identifiant, sinon le curseur saute/double des lignes.
    ordering = ("-created_at", "-id")
