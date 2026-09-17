"""Mélanges pour `django-admin` — pensés pour ne pas casser à la montée de version de Django."""

from __future__ import annotations

from django.contrib import admin
from django.db import models


class ExplicitUrlSchemeAdminMixin(admin.ModelAdmin):
    """Force `assume_scheme` sur les champs `URLField` générés par l'admin.

    Django 5.0 a introduit le réglage transitoire `FORMS_URLFIELD_ASSUME_HTTPS`, puis l'a déprécié
    dans la même série : le seul endroit stable où poser le choix est le champ de formulaire lui-même.
    Sans cela, la page de changement d'un `QrCode` lève un `RemovedInDjango60Warning`, que nos réglages
    de test convertissent en erreur — et qui, en 6.0, changera la valeur enregistrée d'une URL saisie
    sans schéma. Le schéma reste validé côté modèle par `apps/qr/validators.py`.
    """

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, models.URLField):
            kwargs.setdefault("assume_scheme", "https")
        return super().formfield_for_dbfield(db_field, request, **kwargs)
