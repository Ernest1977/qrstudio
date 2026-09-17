"""Compte utilisateur.

Choix de modèle : `AbstractUser` (pas `AbstractBaseUser`) parce que l'admin Django, allauth et
`contrib.auth` s'y branchent sans correctif. `username` est conservé mais **éginal à l'e-mail** :
le supprimer casserait `contrib.admin` (recherche, filtres) pour un gain nul.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import timedelta
from typing import Any, ClassVar

from django.contrib.auth.models import AbstractUser
from django.core.validators import MinLengthValidator
from django.db import models
from django.utils import timezone

from apps.accounts.managers import UserManager, normalize_email


class Plan(models.TextChoices):
    # Les identifiants sont stables (ils atterrissent dans les jetons, les factures, les journaux) ;
    # les libellés, non. Les prix et ce que chaque ligne ouvre vivent dans `apps/accounts/plans.py` —
    # un `choices` de modèle n'est pas l'endroit où mettre une grille tarifaire.
    FREE = "free", "Gratuit"
    STANDARD = "standard", "Standard"
    PREMIUM = "premium", "Premium"
    BUSINESS = "business", "Entreprise"


class User(AbstractUser):
    email = models.EmailField("adresse e-mail", unique=True)
    username = models.CharField(max_length=255, unique=True, editable=False)

    is_email_verified = models.BooleanField("e-mail vérifié", default=False)
    is_suspended = models.BooleanField("suspendu", default=False)
    suspension_reason = models.TextField("motif de suspension", blank=True)

    locale = models.CharField(max_length=10, default="fr")
    timezone_name = models.CharField("fuseau", max_length=64, default="Europe/Paris")

    plan = models.CharField(max_length=16, choices=Plan.choices, default=Plan.FREE)
    plan_until = models.DateTimeField("plan valable jusqu'au", null=True, blank=True)

    # Le niveau de QR statiques **deja acquis** au moment ou la grille a change (20 -> 1 pour le Gratuit,
    # le 2026-09-16). Un gel ne peut que protéger ce que le compte avait ; il ne rabat jamais le plafond
    # d'un palier plus haut, et il ne s'applique plus des que le palier courant change de nom.
    quota_statique_gele = models.PositiveIntegerField("QR statiques (niveau acquis)", null=True, blank=True)
    quota_statique_palier = models.CharField(max_length=16, blank=True, default="")

    # Base légale du suivi par pays (§9 ARCHITECTURE.md) : horodatée, pas un simple booléen.
    consent_tracking_at = models.DateTimeField("consentement mesure, à", null=True, blank=True)
    consent_ip_hash = models.CharField(max_length=64, blank=True, editable=False)
    marketing_opt_in = models.BooleanField("emails marketing", default=False)

    # `ClassVar` explicite : django-stubs déclare `objects` sur la classe de base et refuse qu'un
    # attribut de classe devienne un attribut d'instance. Les deux `ignore` ciblés sont étroits (pas
    # de `# type: ignore` seul) parce que `warn_unused_ignores` est actif : ils disparaîtront d'eux-
    # mêmes si le plugin corrige sa vision de l'héritage.
    objects: ClassVar[UserManager] = UserManager()  # type: ignore[assignment]

    # Redéclaration simple : c'est l'attribut imposé par Django pour un modèle à e-mail comme
    # identifiant. Les stubs le déclarent tantôt d'instance tantôt de classe selon la version ; quand
    # ils changent d'avis, l'`ignore` devient inutile et `warn_unused_ignores` le signale — c'est
    # voulu, et plus honnête qu'un ignore permanent.
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        verbose_name = "compte"
        verbose_name_plural = "comptes"
        ordering = ("-date_joined",)

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        if not self.username:
            self.username = self.email
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.email

    @property
    def can_track(self) -> bool:
        """Le propriétaire a-t-il accepté la mesure d'audience (seule source des stats par pays) ?"""
        return self.is_active and self.consent_tracking_at is not None

    # Attribues a la volee dans `plan_effectif`, donc annotes ici : mypy ne devine pas le type d'un
    # attribut cree dans une methode, et ce chemin est lu a chaque appel d'API.
    _abonnement_cache: Any = None
    _abonnement_recherche: bool = False

    @property
    def plan_effectif(self) -> str:
        """Le plan réellement applicable **maintenant**.

        Un abonnement payé dont `plan_until` est passé doit redescendre en gratuit sans qu'une tâche de
        nuit n'ait eu le temps de le faire : sinon le client continue de consommer le quota premium
        après une échéance de carte refusée. Le champ `plan`, lui, reste ce qui est facturé.
        """
        code = self.plan or Plan.FREE
        # Quand un abonnement existe, c'est LUI qui décide : il porte le sursis en cas de paiement en
        # retard et l'annulation, que le seul `plan_until` ne sait pas exprimer. Le champ `plan` reste
        # écrit en miroir par `billing.services.accord` pour les lectures chaudes (cache de
        # résolution, quotas), mais il ne fait pas foi.
        from apps.billing.models import Abonnement

        if not self._abonnement_recherche:  # un seul aller-retour SQL par objet, meme si la reponse est vide
            self._abonnement_recherche = True
            self._abonnement_cache = Abonnement.objects.filter(user_id=self.pk).first()
        abo = self._abonnement_cache
        if abo is not None:
            return abo.palier if abo.en_registre else Plan.FREE
        if code == Plan.FREE:
            return code
        if self.plan_until and timezone.now() > self.plan_until:
            return Plan.FREE
        return code

    def refresh_from_db(
        self,
        using: str | None = None,
        fields: Iterable[str] | None = None,
        from_queryset: Any = None,
    ) -> None:
        # Le cache de `plan_effectif` vit sur l'instance : sans invalidation ici, un objet relu
        # (typiquement apres un webhook qui vient de changer l'abonnement) garde l'ancienne decision
        # d'acces — exactement le genre d'erreur qui ne se voit qu'en production.
        self.__dict__.pop("_abonnement_cache", None)
        self.__dict__.pop("_abonnement_recherche", None)
        # `from_queryset` est passe par Django lui-meme (`.in_bulk`, `prefetch`): le perdre ici
        # rendrait la relecture plus lente sans le dire, d'ou la transmission explicite.
        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)

    def a_droit_a(self, caracteristique: str) -> bool:
        """Ce que le compte peut utiliser. L'exemption d'administration passe avant la grille.

        Sans ca, un super-utilisateur exonere qui cree des QR depuis l'espace admin se heurte aux portes
        `plan_required` de l'export PDF et des stats : la creation serait gratuite, son exploitation non.
        """
        from apps.accounts import plans
        from apps.accounts.exemption import exonere_de_facturation

        if exonere_de_facturation(self):
            return True
        return plans.a_caracteristique(self.plan_effectif, caracteristique)

    @property
    def limites(self) -> dict:
        from apps.accounts import plans

        p = plans.palier(self.plan_effectif)
        return {nom: plans.limite(self.plan_effectif, nom) for nom in p.limites}

    @property
    def quota_dynamic(self) -> int:
        """QR dynamiques autorisés sur 30 jours glissants ; 0 = le palier ne l'inclut pas du tout."""
        from apps.accounts import plans

        valeur = plans.limite(self.plan_effectif, "dynamiques_30j", 0)
        return 1_000_000_000 if valeur is None else valeur

    @property
    def quota_statique(self) -> int:
        """Le plafond applicable : celui du palier, sauf si un gel **plus favorable** le dépasse.

        Trois regles, dans cet ordre :
        * `None` (illimite, Business) domine tout — on ne rabote pas un palier paye ;
        * un gel ne vaut que pour le palier ou il a ete pose : un compte gratuit grele a 20 qui passe en
          Premium repart du plafond Premium, sinon le gel deviendrait un plancher perpetuel ;
        * a palier egal, on prend le **max** : c'est ce qui rend la baisse du Gratuit non retroactive
          sans empecher une hausse future de beneficier aux memes comptes.
        """
        from apps.accounts import plans

        valeur = plans.limite(self.plan_effectif, "statiques", 1)
        if valeur is None:
            return 1_000_000_000
        gelee = self.quota_statique_gele
        if gelee is not None and self.quota_statique_palier == self.plan_effectif:
            valeur = max(int(gelee), valeur)
        return valeur

    def geler_quota_statique(self, *, valeur: int, palier: str) -> None:
        """Enregistre le niveau acquis (appelee par la migration de changement de grille, pas par le metier)."""
        self.quota_statique_gele = int(valeur)
        self.quota_statique_palier = palier
        self.save(update_fields=["quota_statique_gele", "quota_statique_palier"])

    @property
    def jours_historique(self) -> int:
        from apps.accounts import plans

        valeur = plans.limite(self.plan_effectif, "historique_jours", 0)
        return 3650 if valeur is None else valeur

    @property
    def is_blocked(self) -> bool:
        return self.is_suspended or not self.is_active


class EmailVerificationCode(models.Model):
    """Code à 6 chiffres, 15 minutes, 5 tentatives, puis verrouillage du canal 30 minutes.

    `consumed_at` plutôt que suppression de la ligne : un incident « on m'a volé ma vérification »
    se diagnostique avec la trace, pas avec un `DELETE`.
    """

    CODE_LENGTH = 6
    VALID_FOR = timedelta(minutes=15)
    MAX_ATTEMPTS = 5
    LOCKOUT = timedelta(minutes=30)

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="verification_codes")
    code = models.CharField(max_length=6, validators=[MinLengthValidator(CODE_LENGTH)])
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        etat = "consomme" if self.consumed_at else ("verrouille" if self.is_locked else "en attente")
        return f"code {self.user_id} {etat}"

    class Meta:
        verbose_name = "code de vérification"
        verbose_name_plural = "codes de vérification"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["user", "created_at"])]

    @classmethod
    def issue(cls, user: User) -> EmailVerificationCode:
        """Nouveau code ; les précédents non consommés sont invalidés (un seul canal vivant)."""
        cls.objects.filter(user=user, consumed_at__isnull=True).update(consumed_at=timezone.now())
        return cls.objects.create(user=user, code=f"{secrets.randbelow(1_000_000):06d}")

    @property
    def expires_at(self):
        return self.created_at + self.VALID_FOR

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and timezone.now() < self.locked_until)

    @property
    def attempts_left(self) -> int:
        return max(0, self.MAX_ATTEMPTS - self.attempts)

    def matches(self, candidate: str) -> bool:
        """Comparaison en temps constant, puis comptage des tentatives et verrouillage."""
        import hmac

        if self.consumed_at or self.is_expired or self.is_locked:
            return False
        ok = hmac.compare_digest(str(candidate or ""), self.code)
        self.attempts += 1
        fields = ["attempts"]
        if not ok and self.attempts >= self.MAX_ATTEMPTS:
            self.locked_until = timezone.now() + self.LOCKOUT
            fields.append("locked_until")
        self.save(update_fields=fields)
        return ok

    def mark_verified(self) -> None:
        from django.db import transaction

        with transaction.atomic():
            self.consumed_at = timezone.now()
            self.save(update_fields=["consumed_at"])
            User.objects.filter(pk=self.user_id).update(is_email_verified=True)
