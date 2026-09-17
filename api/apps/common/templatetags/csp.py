"""Bibliotheque `{% load csp %}` : la balise de nonce des gabarits.

Le contrat de Django est strict — `get_package_libraries` ne retient qu'un module qui expose un nom
`register` : la definition de la balise vit donc ici, et la logique (politique, nonce, mode) reste dans
`apps.common.csp`, testable sans moteur de gabarits.
"""

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.common.csp import valeur_du_nonce

register = template.Library()


@register.simple_tag(takes_context=True)
def csp_nonce(context: dict) -> str | SafeString:
    """Rend `nonce="…"` dans la balise appelee : `<style {% csp_nonce %}>`.

    Rien du tout si la requete manque (e-mails, rendus de test) — voir `valeur_du_nonce`. Et `format_html`
    suffit a fabriquer la chaine sure : passer par `mark_safe` ici serait un `mark_safe` de plus a relire,
    pour une chaine que le generateur echappe deja.
    """
    nonce = valeur_du_nonce(context.get("request") if context else None)
    return "" if nonce is None else format_html('nonce="{}"', nonce)
