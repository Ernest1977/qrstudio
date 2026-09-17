"""Rendus vérifiés : le style artistique et l'animation, côté serveur, relus avant d'être servis.

Le studio du front dessine un aperçu instantané ; ce que cet endpoint livre, c'est le **fichier** — celui
qu'on imprime, qu'on poste, qu'on affiche sur une borne. D'où trois règles :

* la capacité est vérifiée ici (`qr_artistique_ia`, `qr_anime`) avec un 402 qui nomme le palier à vendre,
  calculé depuis la grille et non écrit à la main ;
* la réponse porte le résultat de la relecture (`X-Lisibilite`, `X-Score`, `X-Montage`) : le client voit
  si le rendu a dû replier sur un style plus prudent ou réduire le logo, au lieu de l'apprendre au moment
  de l'impression ;
* tout est mis en cache par *contenu encodé + paramètres de dessin*, jamais par `id` : deux comptes qui
  partagent un payload partagent l'image, et un QR dynamique garde la sienne à vie (le slug ne bouge pas).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from typing import TYPE_CHECKING

from django.http import HttpResponse
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common import throttling
from apps.common.exceptions import ApiError

logger = logging.getLogger(__name__)

TAILLE_LOGO_MAX = 256 * 1024
# La surface, pas seulement le poids : un PNG de 40 octets peut se declarer en 65535x65535 et `load()`
# allouerait ~17 Go. Le logo est de toute facon ramene a 256² — 16 Mpx lui laissent une marge de 2 500 x.
PIXELS_LOGO_MAX = 16 * 1000 * 1000
FORMATS_LOGO = {"image/png", "image/jpeg", "image/webp"}
FORMATS_BASE = {"png", "svg", "pdf"}


def exiger_capacite(user, caracteristique: str, *, quoi: str) -> None:
    """402 `plan_required` avec le palier à vendre, déduit de la grille.

    Le libellé vient de `plans.palier_minimum()` : un message écrit à la main dans la vue est la promesse
    qu'un jour la grille change et que le 402 envoie le client acheter le mauvais palier.
    """
    from apps.accounts import plans

    if user.a_droit_a(caracteristique):
        return
    requis = plans.palier_minimum(caracteristique)
    palier = plans.palier(requis)
    raise ApiError(
        "plan_required",
        f"{quoi} est réservé au palier {palier.nom} ({palier.prix_eur} EUR/mois).",
        status_code=402,
        details={"caracteristique": caracteristique, "palier_requis": requis, "plan_actuel": user.plan_effectif},
    )


def contenu_encode(qr) -> str:
    """Ce que le scanner va lire : l'URL courte pour un dynamique, le texte pour un statique."""
    from apps.qr.models import Kind

    return qr.short_url if qr.kind == Kind.DYNAMIC else qr.payload


def cle_cache(qr, *, contenu: str | None = None, **reglages) -> str:
    """`contenu` explicité : l'aperçu sans QR enregistré n'a pas d'objet à lire."""
    """Clé dérivée du contenu **et de chaque paramètre de dessin**.

    Oublier un paramètre ici fait servir à un client le GIF d'un autre (même payload, autre nombre de
    frames). La liste est donc explicite, et le `fmt` y figure parce qu'un PNG et un GIF du même contenu
    ne sont pas la même image.
    """
    briques = [contenu if contenu is not None else contenu_encode(qr)]
    briques += [f"{nom}={reglages.get(nom, '')}" for nom in sorted(reglages)]
    return "qrs:qrart:" + hashlib.sha256("|".join(briques).encode()).hexdigest()[:32]


def _entier(request, nom: str, defaut: int) -> int:
    brut = request.query_params.get(nom)
    if brut is None and hasattr(request, "data") and isinstance(request.data, dict):
        brut = request.data.get(nom)
    if brut in (None, ""):
        return defaut
    try:
        return int(brut)
    except (TypeError, ValueError):
        raise ApiError("parametre_invalide", f"`{nom}` doit être un entier.") from None


def _chaine(request, nom: str, defaut: str = "", *, maxi: int = 64) -> str:
    brut = request.query_params.get(nom)
    if brut is None and hasattr(request, "data") and isinstance(request.data, dict):
        brut = request.data.get(nom)
    return str(brut if brut not in (None, "") else defaut)[:maxi]


def logo_de(request) -> tuple[bytes | None, str]:
    """Le logo fourni en `multipart/form-data`, remis à l'échelle en PNG 256² et identifié par son hash.

    Deux logos différents ne doivent jamais partager une entrée de cache : se fier au **nom du fichier**
    (cas d'usage : tout le monde l'appelle `logo.png`) ferait servir le logo d'un autre compte.
    """
    fichier = request.FILES.get("logo") if hasattr(request, "FILES") else None
    if fichier is None:
        return None, ""
    if fichier.size > TAILLE_LOGO_MAX:
        raise ApiError(
            "logo_trop_lourd",
            f"Le logo dépasse {TAILLE_LOGO_MAX // 1024} Ko.",
            status_code=413,
            details={"octets": fichier.size, "max": TAILLE_LOGO_MAX},
        )
    if (getattr(fichier, "content_type", "") or "").lower() not in FORMATS_LOGO:
        raise ApiError(
            "logo_format_invalide",
            "Logo attendu en PNG, JPEG ou WebP.",
            details={"recu": getattr(fichier, "content_type", "") or "inconnu", "attendus": sorted(FORMATS_LOGO)},
        )
    brut = fichier.read()
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(brut)) as image:
            # `image.size` se lit dans l'en-tete, sans decoder : c'est le seul moment ou refuser une
            # bombe de decompression coute rien. Le seuil de Pillow (178 Mpx) est trop haut pour une
            # machine qui tient aussi les redirections.
            if image.size[0] * image.size[1] > PIXELS_LOGO_MAX:
                raise ApiError(
                    "logo_trop_grand",
                    f"Image de {image.size[0]}×{image.size[1]} pixels refusée : la surface dépasse "
                    f"{PIXELS_LOGO_MAX // 1_000_000} mégapixels. Redimensionnez le logo avant l'envoi.",
                    details={"pixels": image.size[0] * image.size[1], "max": PIXELS_LOGO_MAX},
                )
            image.load()
            # `Resampling.LANCZOS` depuis Pillow 9.1 ; l'ancien nom `Image.LANCZOS` est deconstitue chez
            # les plus recents : le tirer depuis `Image.Resampling` quand il existe evite un 500 sur
            # l'hote qui a monte Pillow sans relire notre appel.
            reech = getattr(Image, "Resampling", Image).LANCZOS
            carree = ImageOps.fit(image, (256, 256), method=reech, bleed=0.02)
            tampon = io.BytesIO()
            carree.convert("RGBA").save(tampon, format="PNG", optimize=True)
    except ApiError:
        # Notre propre refus doit sortir tel quel : l'attraper ici le transformerait en
        # `logo_ilisible`, et l'appelant ne saurait plus s'il doit redimensionner ou changer de fichier.
        raise
    except Image.DecompressionBombError as exc:
        # Pillow refuse lui-meme au-dela de ~178 Mpx, avant meme que notre controle passe : le traduire
        # dans notre vocabulaire, sinon l'utilisateur lit « ce n'est pas une image lisible » sur un
        # fichier parfaitement valide.
        raise ApiError(
            "logo_trop_grand",
            f"Surface d'image refusee par le decodeur : redimensionnez le logo sous "
            f"{PIXELS_LOGO_MAX // 1_000_000} megapixels.",
            details={"max": PIXELS_LOGO_MAX},
        ) from exc
    except Exception as exc:
        raise ApiError("logo_ilisible", "Le logo fourni n'est pas une image lisible.") from exc
    octets = tampon.getvalue()
    return octets, hashlib.sha256(octets).hexdigest()[:16]


def classique(request, qr, fmt: str = "png") -> HttpResponse:
    """Le rendu de base (png/svg/pdf), partagé par `/image/` et `/rendu/?fmt=png`.

    Une seule implémentation pour les deux URL : sinon le cache, la capacité et le nom de fichier
    peuvent diverger entre « l'URL que le front appelle » et « celle que l'API documente ».
    """
    from django.core.cache import cache

    from apps.qr import render as render_mod

    design = qr.design or {}
    if fmt == "pdf":
        exiger_capacite(request.user, "export_pdf", quoi="L'export PDF")
    # `size` est passe **brut** a `render.rendu` : ce sont lui qui connait les bornes et le code d'erreur
    # (`invalid_size`). Valider ici inventerait un deuxieme contrat d'erreur pour la meme URL.
    size = request.query_params.get("size") or 512
    cle = render_mod.cache_key(
        contenu=contenu_encode(qr),
        fmt=fmt,
        size=int(size) if str(size).isdigit() else 0,
        ecc=str(design.get("ecc") or "M"),
        margin=int(design.get("margin", 2) or 2),
        dark=str(design.get("dark") or "#000000"),
        light=str(design.get("light") or "#ffffff"),
    )
    ok = cache.get(cle)
    if ok is None:
        ok = render_mod.rendu(qr, fmt=fmt, size=size, design=design)
        cache.set(cle, ok, 60 * 60 * 24)
    octets, mime, nom = ok
    return _reponse(octets, mime, nom)


def _reponse(
    octets: bytes, mime: str, nom: str, *, score: dict | None = None, montage: dict | None = None
) -> HttpResponse:
    reponse = HttpResponse(octets, content_type=mime)
    reponse["Content-Disposition"] = f'inline; filename="{nom}"'
    reponse["Cache-Control"] = "public, max-age=86400, immutable"
    if score is not None:
        reponse["X-Lisibilite"] = "verifiee" if score.get("lisible") else "non-verifiee"
        reponse["X-Score"] = json.dumps(score, ensure_ascii=False, separators=(",", ":"))[:1800]
        if score.get("repli"):
            reponse["X-Style-Repli"] = str(score.get("style") or "")
        if score.get("logo_reduit"):
            reponse["X-Logo-Reduit"] = "1"
        if score.get("logo_retire"):
            reponse["X-Logo-Retire"] = "1"
    if montage is not None:
        reponse["X-Montage"] = json.dumps(montage, ensure_ascii=False, separators=(",", ":"))[:1200]
    return reponse


class RendusMixin:
    """`GET|POST /api/v1/qr/{id}/rendu/?fmt=art|gif` et `GET /api/v1/qr/styles/`.

    Le mixin herite de `self.get_object()` chez son hote (`QrViewSet`, via `GenericViewSet`). Le contrat
    est declare sous un `if TYPE_CHECKING:` **dans le corps** : une veritable methode ici passerait devant
    celle du viewset dans l'MRO — le mixin est en premiere base — et renverrait un
    `NotImplementedError` sur chaque requete (et une classe de garde heritee, un `NameError` au
    demarrage : le test de deploiement vient de nous l'apprendre deux fois).
    """

    if TYPE_CHECKING:

        def get_object(self): ...

    @action(detail=False, methods=["get"], url_path="styles")
    def styles(self, request):
        """Les styles disponibles avec leur contraste : le front affiche, il ne duplique pas la table."""
        from apps.qr import art

        return Response({"styles": art.styles_disponibles(), "logo_surface_max": art.LOGO_SURFACE_MAX})

    @action(detail=False, methods=["get", "post"], url_path="apercu", throttle_classes=[throttling.QrArtThrottle])
    def apercu(self, request):
        """Le rendu d'un contenu **non enregistré** : l'aperçu vérifié du studio, avant de créer quoi que ce soit.

        Sans cet endpoint, le studio ne pourrait montrer le style artistique qu'après avoir créé un QR —
        et le Gratuit, qui n'a droit qu'à un seul QR, paierait le droit de regarder. Ici le contenu est
        fourni, vérifié, dessiné, relu : rien n'est enregistré, et l'URL reste une `<img src>` légale.

        Le contenu est toujours traité comme **statique** : un aperçu d'un QR dynamique exigerait le lien
        court, donc la création. Le client qui veut l'art sur un dynamique le demande sur `/rendu/`.
        """
        from django.conf import settings
        from django.core.cache import cache

        from apps.qr import animation, art

        fmt = _chaine(request, "fmt", "art", maxi=8).lower()
        if fmt not in {"art", "gif"}:
            raise ApiError(
                "invalid_format", "`/apercu/` ne rend que `art` et `gif`.", details={"supportes": ["art", "gif"]}
            )
        exiger_capacite(
            request.user,
            "qr_artistique_ia" if fmt == "art" else "qr_anime",
            quoi="Le QR artistique" if fmt == "art" else "Le QR animé",
        )

        contenu = request.data.get("payload") if hasattr(request, "data") and isinstance(request.data, dict) else None
        contenu = str(contenu if contenu not in (None, "") else request.query_params.get("payload") or "")
        if not contenu.strip():
            raise ApiError("payload_requis", "`payload` est requis (le contenu à encoder).", status_code=400)
        limite = int((settings.QR or {}).get("MAX_PAYLOAD_BYTES", 2953))
        if len(contenu.encode("utf-8")) > limite:
            raise ApiError(
                "payload_trop_long",
                f"Le contenu dépasse {limite} octets : un QR ne peut pas plus, aperçu ou pas aperçu.",
                details={"octets": len(contenu.encode("utf-8")), "max": limite},
            )
        taille = _entier(request, "size", 512)
        style = _chaine(request, "style", "naples", maxi=24).lower()
        if fmt == "art":
            # `logo=""` explicite : la cle doit avoir exactement les memes touches que le telechargement
            # d'un QR enregistre, sinon les deux chemins redessinent le meme contenu (deux CPU, deux entre.es).
            cle = cle_cache(None, contenu=contenu, fmt=fmt, taille=taille, style=style, logo="")
            ok = cache.get(cle)
            if ok is None:
                octets, mime, score = art.rendre(None, contenu=contenu, style=style, taille=taille)
                ok = (octets, mime, "apercu-artistique.png", score.pour_api())
                cache.set(cle, ok, 60 * 30)  # l'apercu est volatile : 30 min, pas 24 h
            octets, mime, nom, score = ok
            return _reponse(octets, mime, nom, score=score)

        reglages = {
            "frames": _entier(request, "frames", 24),
            "duree": _entier(request, "duree", 90),
            "liser": _entier(request, "liser", 24),
        }
        accroche = _chaine(request, "accroche", "", maxi=40)
        # Les reglages sont nommes un par un, et non deboites en `**dict[str, int]` : le dictionnaire
        # pourrait porter une cle `contenu` (le parametre est nomme, juste au-dessus), et aucun typeur ne
        # rattrape ce genre de glissement.
        cle = cle_cache(
            None,
            contenu=contenu,
            fmt=fmt,
            taille=taille,
            style=style,
            logo="",
            accroche=accroche,
            frames=reglages["frames"],
            duree=reglages["duree"],
            liser=reglages["liser"],
        )
        ok = cache.get(cle)
        if ok is None:
            octets, mime, montage = animation.rendre(
                None,
                contenu=contenu,
                style=style,
                taille=taille,
                accroche=accroche,
                frames=reglages["frames"],
                duree=reglages["duree"],
                liser=reglages["liser"],
            )
            ok = (octets, mime, "apercu-anime.gif", montage.pour_api())
            cache.set(cle, ok, 60 * 30)
        octets, mime, nom, montage = ok
        return _reponse(octets, mime, nom, montage=montage)

    @action(detail=True, methods=["get", "post"], throttle_classes=[throttling.QrArtThrottle])
    def rendu(self, request, id=None):
        """Le fichier à imprimer ou à publier, avec la preuve de lisibilité dans les en-têtes.

        `POST` sert uniquement à fournir un logo (multipart) ; `GET` couvre les variations de style et
        d'animation, qui doivent rester des URL partageables (un lien de borne Wi-Fi, un `curl` de
        vérification, un `<img src>` dans un e-mail).
        """
        from django.core.cache import cache

        qr = self.get_object()
        fmt = _chaine(request, "fmt", "png", maxi=8).lower()
        if fmt in FORMATS_BASE:
            return classique(request, qr, fmt)

        taille = _entier(request, "size", 512)
        design = qr.design or {}
        style = _chaine(request, "style", str(design.get("art") or "naples"), maxi=24).lower()
        contenu = contenu_encode(qr)
        logo, hash_logo = logo_de(request)

        if fmt == "art":
            exiger_capacite(request.user, "qr_artistique_ia", quoi="Le QR artistique")
            from apps.qr import art

            cle = cle_cache(qr, fmt=fmt, taille=taille, style=style, logo=hash_logo)
            ok = cache.get(cle)
            if ok is None:
                octets, mime, score = art.rendre(qr, contenu=contenu, style=style, taille=taille, logo=logo)
                ok = (octets, mime, f"qr-{qr.slug or qr.pk}-{style}.png", score.pour_api())
                cache.set(cle, ok, 60 * 60 * 24)
            octets, mime, nom, score = ok
            return _reponse(octets, mime, nom, score=score)

        if fmt == "gif":
            exiger_capacite(request.user, "qr_anime", quoi="Le QR animé")
            from apps.qr import animation

            reglages = {
                "frames": _entier(request, "frames", int(design.get("frames", 24) or 24)),
                "duree": _entier(request, "duree", int(design.get("duree", 90) or 90)),
                "liser": _entier(request, "liser", int(design.get("liser", 24) or 24)),
            }
            accroche = _chaine(request, "accroche", str(design.get("accroche") or ""), maxi=40)
            cle = cle_cache(
                qr,
                fmt=fmt,
                taille=taille,
                style=style,
                logo=hash_logo,
                accroche=accroche,
                frames=reglages["frames"],
                duree=reglages["duree"],
                liser=reglages["liser"],
            )
            ok = cache.get(cle)
            if ok is None:
                octets, mime, montage = animation.rendre(
                    qr,
                    contenu=contenu,
                    style=style,
                    taille=taille,
                    logo=logo,
                    accroche=accroche,
                    **reglages,
                )
                ok = (octets, mime, f"qr-{qr.slug or qr.pk}-anime.gif", montage.pour_api())
                cache.set(cle, ok, 60 * 60 * 24)
            octets, mime, nom, montage = ok
            return _reponse(octets, mime, nom, montage=montage)

        raise ApiError(
            "invalid_format",
            "Format non supporté par `/rendu/`.",
            details={"supportes": sorted(FORMATS_BASE | {"art", "gif"})},
        )
