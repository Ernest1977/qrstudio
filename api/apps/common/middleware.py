"""Middlerware communs : IP client, durcissement de l'admin, politique de contenu (CSP)."""

from __future__ import annotations

import time

from django.conf import settings

from apps.common import csp
from apps.common import ip as ip_lib


class ClientIPMiddleware:
    """Pose `request.client_ip` une seule fois, avec la confiance proxy décidée par l'environnement."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.trust_proxy = getattr(settings, "TRUST_PROXY_HEADERS", True)

    def __call__(self, request):
        request.client_ip = ip_lib.client_ip(request, trust_proxy=self.trust_proxy)
        return self.get_response(request)


class MfaGateMiddleware:
    """Barre `django-admin` au personnel sans second facteur (TOTP) — cf. staff_access.

    On redirige vers la page d'activation d'allauth plutôt que de renvoyer un 403 sec : un
    administrateur légitime doit pouvoir se conformer sans ouvrir un ticket.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.http import HttpResponseForbidden
        from django.shortcuts import redirect
        from django.urls import NoReverseMatch, reverse

        from apps.common.staff_access import staff_mfa_required

        if staff_mfa_required(request):
            try:
                return redirect(reverse("mfa_index"))
            except NoReverseMatch:  # allauth.mfa retiré du déploiement : on refuse, on n'ouvre pas
                return HttpResponseForbidden("Second facteur requis pour l'administration.")
        return self.get_response(request)


class AdminHardeningMiddleware:
    """Cache-control `no-store` sur l'admin + mesure de durée (les dérives se voient dans les logs).

    La 2FA du personnel est contrôlée dans `apps.common.staff_access`, pas ici : ce middleware
    tourne aussi pour les vues publiques, et un `hasattr(request, 'user')` y est faux quand une vue
    asynchrone s'en passe.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.prefix = "/" + settings.ADMIN_URL.strip("/") + "/"

    def __call__(self, request):
        is_admin = request.path.startswith(self.prefix)
        started = time.monotonic()
        response = self.get_response(request)
        if is_admin:
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            elapsed_ms = (time.monotonic() - started) * 1000
            response.headers["X-Admin-Duration-Ms"] = f"{elapsed_ms:.0f}"
        return response


class CspMiddleware:
    """Politique de contenu sur les pages HTML, avec un nonce propre à la requête (cf. `apps.common.csp`).

    Deux choses sont volontairement ici et pas ailleurs :

    * `request.csp_nonce` est posé **avant** la vue, pour que le gabarit le trouve ;
    * l'en-tête n'est posé que sur du `text/html`. Un `HttpResponseRedirect` du chemin chaud `/r/{slug}`
      ne paie donc ni `secrets.token_urlsafe` (l'objet est paresseux, il ne s'engendre que lu) ni
      concaténation de politique.

    Le mode est résolu à la construction du middleware : une valeur `CSP_MODE` mal orthographiée empêche
    le serveur de démarrer plutôt que de laisser filer une politique silencieusement absente.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.mode = csp.resoudre_mode(getattr(settings, "CSP_MODE", ""), debug=settings.DEBUG)

    def __call__(self, request):
        request.csp_nonce = csp.Nonce()
        reponse = self.get_response(request)
        if self.mode == "off" or not csp.est_html(reponse):
            return reponse

        signaler = bool(getattr(settings, "CSP_SIGNALER", False))
        entete, valeur = csp.politique(
            str(request.csp_nonce),
            mode=self.mode,
            script_extra=getattr(settings, "CSP_SCRIPT_SRC_EXTRA", ""),
            montee_insegure=bool(getattr(settings, "SECURE_SSL_REDIRECT", False)),
            signaler=signaler,
        )
        reponse[entete] = valeur
        if signaler:
            # `report-to` a besoin d'un nom de groupe résolu en URL absolue ; `report-uri` porte le chemin.
            reponse["Reporting-Endpoints"] = f'{csp.GROUPE_RAPPORT}="{request.build_absolute_uri(csp.CHEMIN_RAPPORT)}"'
        return reponse


def is_staff_request(request) -> bool:
    """Garde appelable depuis une vue : réservé au personnel."""
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_staff)
