"""Les identifiants de types acceptés côté serveur.

Le front (`qr-coding-react/src/lib/types.js`) est la source de vérité du *contenu* ; ici on ne
veut que la liste des clés, pour refuser un `type_id` inventé par un script. La liste est **testée
contre le front** (tests/test_types_parity.py) : si le front ajoute un type et que le back ignore,
le test rouge prévient avant la mise en production.
"""

from __future__ import annotations

KNOWN_TYPE_IDS = frozenset(
    {
        "url",
        "instagram",
        "facebook",
        "x",
        "tiktok",
        "youtube",
        "telegram",
        "snapchat",
        "pinterest",
        "linkedin",
        "whatsapp",
        "bcard",
        "pdf",
        "word",
        "excel",
        "image",
        "text",
        "wifi",
        "tel",
        "sms",
        "mail",
        "event",
    }
)
