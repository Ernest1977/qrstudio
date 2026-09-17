"""La règle unique : qui ne paie rien, et pourquoi.

Trois endroits décident de l'argent — `qr.quota.exiger_capacite` (402), `User.a_droit_a` (fonctionnalités
payantes, donc l'export PDF et les stats), et le front via `/auth/me`. Chacun avait sa propre formulation
de « le staff n'est pas facturé » : `is_staff` ici, rien là. C'est exactement la forme que prend un bug
de facturation silencieux : le back-office peut créer un QR sans que ses propres stats lui répondent, ou
l'inverse — un compte de démonstration qui facture.

La règle est donc ici, et elle est écrite une seule fois :

- un **superutilisateur** n'est jamais facturé : il administre l'instance, il n'est pas un client ;
- un membre du personnel qui porte la permission `qr.creer_sans_facturation` ne l'est pas non plus —
  permission **nommément accordée**, pas héritée de `is_staff` : sinon tout compte de support, de staging
  ou de démo devient un trou dans les revenus ;
- personne d'autre. L'exemption est portée par le compte, pas par le chemin d'entrée : un QR créé depuis
  l'API par un compte exonéré est exonéré de la même façon.
"""

from __future__ import annotations

PERMISSION_EXEMPTION = "qr.creer_sans_facturation"


def exonere_de_facturation(user) -> bool:
    """Vrai si la grille tarifaire ne s'applique pas à ce compte. Sans effet de bord, appelable souvent."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return bool(user.has_perm(PERMISSION_EXEMPTION))
