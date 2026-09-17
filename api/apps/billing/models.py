"""Ce que le client paie, et la preuve qu'il l'a payé.

Deux principes de conception, parce que la facturation est l'endroit où les systèmes pourrissent le
mieux :

* **le plan n'est jamais fixé par une requête du client**. Le front demande un lien de paiement ; le
  plan n'est accordé que par le webhook du fournisseur, recoupé avec le `metadata` que *nous* avons
  posé sur la session. Un `POST {"plan": "business"}` qui fonctionne est une faille à 15,99 € par mois
  de manque à gagner par attaquant diligent ;
* **le webhook est idempotent par identifiant d'événement**. Stripe (comme n'importe qui) rejoue.
  `EvenementPaiement` est la table qui rend le rejeu inoffensif — sans contrainte d'unicité dessus, la
  deuxième livraison de `customer.subscription.updated` repousse `plan_until` une deuxième fois, et le
  client qui n'a rien signé se retrouve créditeur de trois mois.

Les montants sont en centimes, alignés sur `apps/accounts/plans.py` (voir la garde monétaire dans
`services.appliquer_evenement`).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Fournisseur(models.TextChoices):
    AUCUN = "none", "Aucun (manuel)"
    STRIPE = "stripe", "Stripe"
    MOBILE = "mobile_money", "Paiement mobile"


class Statut(models.TextChoices):
    ESSAI = "trialing", "Période d'essai"
    ACTIF = "active", "Actif"
    PAIEMENT_EN_RETARD = "past_due", "Paiement en retard"
    PAUSE = "paused", "En pause"
    ANNULE = "canceled", "Annulé"
    INCOMPLET = "incomplete", "Paiement initial non abouti"


class Abonnement(models.Model):
    """L'état courant du partenariat payé d'un compte. Un compte, un abonnement actif."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="abonnement", primary_key=True
    )
    fournisseur = models.CharField(max_length=16, choices=Fournisseur.choices, default=Fournisseur.AUCUN)
    statut = models.CharField(max_length=24, choices=Statut.choices, default=Statut.INCOMPLET, db_index=True)
    palier = models.CharField(max_length=16, default="free", help_text="Ce qui est payé — `User.plan`.")
    # Identifiants côté fournisseur : nécessaires pour le portail client et pour recouper un webhook.
    client_id = models.CharField(max_length=128, blank=True, db_index=True)
    abonnement_fournisseur_id = models.CharField(max_length=128, blank=True, db_index=True)
    prix_id = models.CharField(max_length=128, blank=True)
    debut = models.DateTimeField(null=True, blank=True)
    periode_fin = models.DateTimeField(null=True, blank=True, db_index=True)
    annule_le = models.DateTimeField(null=True, blank=True)
    # Le seul écart de tolérance accepté : un paiement qui échoue (carte expirée, 3-D Secure abandonné)
    # ne doit pas couper le service dans l'heure. Sans `en_sursis`, on aurait le choix entre être
    # brutal ou être généreux gratuitement pour tout le monde.
    en_sursis_jusqu_a = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    maj_le = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "abonnement"
        verbose_name_plural = "abonnements"
        ordering = ["-maj_le"]

    def __str__(self) -> str:
        return f"{self.user_id} — {self.palier} ({self.statut})"

    @property
    def en_registre(self) -> bool:
        """Le palier payé doit-il être accordé maintenant ?

        Trois conditions, pas une : un abonnement annulé ne doit plus rien donner, un abonnement en
        retard donne droit au service jusqu'à la fin du sursis, et un abonnement actif dont la période
        est passée (fournisseur en panne, webhook perdu) ne doit pas être une faveur éternelle.
        """
        if self.statut in {Statut.ANNULE, Statut.INCOMPLET, Statut.PAUSE}:
            return False
        maintenant = timezone.now()
        if self.statut == Statut.PAIEMENT_EN_RETARD:
            return bool(self.en_sursis_jusqu_a and maintenant <= self.en_sursis_jusqu_a)
        return bool(self.periode_fin and self.periode_fin > maintenant)


class EvenementPaiement(models.Model):
    """Journal brut des notifications du fournisseur — la preuve de ce qui a été appliqué.

    `identifiant` est unique : c'est ce qui rend le rejeu inoffensif. On garde la charge entière parce
    qu'un litige de facturation se règle en relisant ce qui a été reçu, pas en rejouant l'API du
    fournisseur (dont les événements expirent).
    """

    identifiant = models.CharField(max_length=128, unique=True)
    fournisseur = models.CharField(max_length=16, choices=Fournisseur.choices, default=Fournisseur.STRIPE)
    type_evenement = models.CharField(max_length=64, db_index=True)
    recu_le = models.DateTimeField(auto_now_add=True)
    charge = models.JSONField(default=dict)
    traite = models.BooleanField(default=False)
    # Une erreur *dans* le traitement est un état à afficher au personnel, pas une exception à
    # renvoyer à Stripe : un 5xx déclenche des rejeus indéfinis sur un événement définitivement faux.
    erreur = models.TextField(blank=True)
    abonnement = models.ForeignKey(
        Abonnement, on_delete=models.SET_NULL, null=True, blank=True, related_name="evenements"
    )

    class Meta:
        verbose_name = "événement de paiement"
        verbose_name_plural = "événements de paiement"
        ordering = ["-recu_le"]

    def __str__(self) -> str:
        return f"{self.type_evenement} — {self.identifiant[:12]}"


class PaiementMobile(models.Model):
    """Demande de paiement par mobile (USSD / SMS / agrégateur), en attente de confirmation.

    Le paiement mobile est **asynchrone par nature** : l'utilisateur approuve sur son téléphone, et le
    service apprend le résultat des minutes plus tard, éventuellement par un canal différent. D'où une
    ligne par tentative avec sa référence et son échéance, plutôt qu'un `redirect` qui suppose une
    réponse immédiate.
    """

    class Statut(models.TextChoices):
        EN_ATTENTE = "pending", "En attente d'approbation"
        CONFIRME = "confirmed", "Confirmé"
        EXPIRE = "expired", "Expiré"
        ECHECHE = "failed", "Échoué"
        REMBOURSE = "refunded", "Remboursé"

    reference = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="paiements_mobiles")
    palier = models.CharField(max_length=16)
    montant_centimes = models.PositiveIntegerField()
    telephone = models.CharField(max_length=32, blank=True)
    fournisseur = models.CharField(max_length=32, blank=True)
    statut = models.CharField(max_length=16, choices=Statut.choices, default=Statut.EN_ATTENTE, db_index=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    expire_le = models.DateTimeField(db_index=True)
    confirme_le = models.DateTimeField(null=True, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "paiement mobile"
        verbose_name_plural = "paiements mobiles"
        ordering = ["-cree_le"]

    def __str__(self) -> str:
        return f"{self.reference} — {self.statut}"

    @property
    def encore_valable(self) -> bool:
        return self.statut == self.Statut.EN_ATTENTE and timezone.now() < self.expire_le
