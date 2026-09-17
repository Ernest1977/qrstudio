"""Createurs d'utilisateurs : l'e-mail est l'identifiant, donc il est **toujours** stocké normalisé.

`CIEmailField`/citext n'est pas nécessaire : on normalise à l'écriture (`strip().lower()`) et on
contraint l'unicité sur la valeur normalisée. Un `UniqueConstraint` sur `LOWER(email)` en plus serait
redondant ici — et poserait un souci sur les comptes créés par une migration SQL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.contrib.auth.models import BaseUserManager


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("Un compte a besoin d'un e-mail.")
        email = normalize_email(email)
        # `cast` assumé : `BaseUserManager` est générique et `self.model` n'est résolu qu'à
        # l'instanciation du gestionnaire. Sans ce lien, le typeur ne sait pas que le modèle sait
        # hacher un mot de passe — et l'alternative (`Any`) supprimerait tout le contrôle du fichier.
        user = cast("User", self.model(email=email, username=email, **extra))
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.full_clean(exclude=["password", "last_login", "date_joined"])
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True or extra.get("is_superuser") is not True:
            raise ValueError("Un superutilisateur doit avoir is_staff et is_superuser à True.")
        user = self._create(email, password, **extra)
        user.is_email_verified = True  # sinon le `django-admin` devient inaccessible au premier login
        user.save(update_fields=["is_email_verified"])
        return user


if TYPE_CHECKING:  # pragma: no cover
    from apps.accounts.models import User
