"""Enveloppe d'erreur stable pour le front : un seul format, partout.

`{"error": {"code": "quota_exceeded", "message": "...", "details": {...}}}` — le front réagit au
`code`, jamais au texte (qui est traduit pour l'humain).
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Erreur métier attendue, avec un code machine et un statut HTTP."""

    def __init__(self, code: str, message: str, *, status_code: int = status.HTTP_400_BAD_REQUEST, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def _payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


def api_exception_handler(exc: Exception, context):
    request = context.get("view").request if context.get("view") else None
    if isinstance(exc, ApiError):
        return Response(_payload(exc.code, exc.message, exc.details), status=exc.status_code)
    if isinstance(exc, Http404):
        return Response(_payload("not_found", "Ressource introuvable."), status=status.HTTP_404_NOT_FOUND)
    if isinstance(exc, PermissionDenied):
        return Response(_payload("forbidden", "Action refusée."), status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, ValidationError):
        return Response(
            _payload("invalid", "Données invalides.", getattr(exc, "message_dict", None) or exc.messages),
            status=status.HTTP_400_BAD_REQUEST,
        )

    from rest_framework.exceptions import NotAuthenticated

    if isinstance(exc, NotAuthenticated):
        # `SessionAuthentication` seul renvoie un 403 ; un front qui gère une page « reconnexion »
        # a besoin du 401 pour ne pas confondre « pas connecté » et « interdit ».
        return Response(_payload("unauthenticated", "Authentification requise."), status=401)

    response = drf_exception_handler(exc, context)
    if response is not None:
        detail = response.data
        code = "invalid"
        if isinstance(detail, dict) and set(detail) == {"detail"}:
            detail = detail["detail"]
        if isinstance(detail, str):
            message, code = detail, "client_error"
        else:
            message = "Données invalides."
        response.data = _payload(code, message, detail if not isinstance(detail, str) else None)
        return response

    # Ici, c'est une erreur inattendue : on loggue l'originale, on ne la montre pas.
    logger.exception("erreur non gérée sur %s", getattr(request, "path", "?"))
    return Response(
        _payload("server_error", "Le service n'a pas pu traiter la demande. Réessayez."),
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
