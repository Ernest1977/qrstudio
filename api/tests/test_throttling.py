"""Le plafond doit s'appliquer même quand la vue oublie de le demander.

C'est la raison d'être du repli `throttle_scope` de classe dans `apps/common/throttling.py` : en DRF,
une vue sans `throttle_scope` n'est **pas** limitée — silencieusement, avec un 200 partout. Oublier
l'attribut sur une route d'authentification est le genre d'erreur que aucune relecture ne voit, et que
seul un test de comportement attrape.

Note de méthode : les taux ne sont pas surchargés ici. `SimpleRateThrottle.THROTTLE_RATES` est figé à
l'import de DRF, donc `settings.REST_FRAMEWORK[...]` en cours de test ne se propage pas ; on travaille
sur les taux réellement configurés (`login` 10/min, `register` 5/h), ce qui rend le test plus vrai
qu'une mocking du dictionnaire.
"""

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common import throttling


class VueSansScope(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [throttling.LoginThrottle]
    # pas de `throttle_scope` : la classe doit suffire

    def get(self, request):
        return Response({"ok": True})


class VueAvecAutreScope(VueSansScope):
    throttle_scope = "register"  # la vue garde la main, et vise un autre plafond


def _statuts(vue, rf, *, ip, n):
    from django.core.cache import cache

    cache.clear()
    sortie = []
    for _ in range(n):
        request = rf.get("/x", REMOTE_ADDR=ip)
        request.client_ip = ip
        sortie.append(vue(request).status_code)
    return sortie


def test_le_plafond_de_la_classe_s_applique_malgre_l_oubli_du_scope(rf):
    statuts = _statuts(VueSansScope.as_view(), rf, ip="203.0.113.9", n=12)
    assert statuts.count(429) == 2, f"login=10/min attendu, obtenu: {statuts}"


def test_le_scope_de_la_vue_garde_la_main(rf):
    """Même classe de throttle, portée différente : 6 requêtes passent sous `login` (10/min) et
    bloquent sous `register` (5/h). Si la vue cessait de primer, le premier blocage apparaîtrait à la
    6ᵉ requête pour les deux — ici il n'apparaît que pour celle qui a déclaré son scope."""
    sans = _statuts(VueSansScope.as_view(), rf, ip="203.0.113.11", n=6)
    avec = _statuts(VueAvecAutreScope.as_view(), rf, ip="203.0.113.11", n=6)
    assert sans.count(429) == 0
    assert avec.count(429) == 1


def test_la_cle_est_l_ip_et_pas_le_compte(rf, user):
    """Deux comptes derrière la même IP partagent le compteur : sinon le bourrage d'identifiants
    devient gratuit dès qu'on devine un compte valide (le plafond serait *par compte visé*)."""
    from django.contrib.auth.models import AnonymousUser
    from django.core.cache import cache

    cache.clear()
    throttle = throttling.QrImageThrottle()
    vue = type("V", (), {"throttle_scope": "login"})  # login: 10/min

    autorise = 0
    for i in range(14):
        request = rf.get("/x", REMOTE_ADDR="198.51.100.7")
        request.client_ip = "198.51.100.7"
        request.user = user if i % 2 else AnonymousUser()
        if throttle.allow_request(request, vue):
            autorise += 1
    assert autorise == 10, f"compteur cloisonné par compte: {autorise} autorisations sur 14 requêtes"


def test_ident_de_secours_quand_le_proxy_n_a_pas_pose_client_ip(rf):
    """`ClientIPMiddleware` peut être absent (vue de test, middleware désactivé en incident) :
    la clé retombe sur `REMOTE_ADDR`, jamais sur une valeur vide partagée par tout le monde."""
    from django.core.cache import cache

    cache.clear()
    throttle = throttling.QrImageThrottle()
    request = rf.get("/x")
    assert not hasattr(request, "client_ip")
    cle = throttle.get_cache_key(request, type("V", (), {"throttle_scope": "qr_image"}))
    assert cle and "127.0.0.1" in cle
