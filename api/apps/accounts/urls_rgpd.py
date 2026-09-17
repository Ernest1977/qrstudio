"""Routes du compte cote client: export et effacement (RGPD).

Separées de `urls.py` (qui est sous `/api/v1/auth/`, le cycle de vie de la session) parce que ces
deux-la ne sont pas de l'authentification : elles portent sur les *donnees* du compte et se branchent
sous `/api/v1/account/`. Un `sessionid` vole ne doit pas devenir un droit de lire un export par
hasard de chemin commun — les regles de frein et de re-authentification sont ici beaucoup plus dures.
"""

from django.urls import path

from apps.accounts import views

urlpatterns = [
    # Pas de slash final: tout le cycle `/api/v1/auth/*` du projet s'en passe, et un `APPEND_SLASH`
    # en 301 sur une route *destructrice* est plus qu'un détail — un client qui suit la redirection
    # sans le corps perdrait le mot de passe de confirmation (et `DELETE` devient un `GET` 405).
    path("", views.AccountDeleteView.as_view(), name="account"),
    # Alias sans slash final: `CommonMiddleware` repond un 301 a `/api/v1/account`, et un 301 sur une
    # route destructive est piégeux — le corps (le mot de passe de confirmation) est perdu au
    # rejeu du client, qui obtient alors un 405 au lieu d'un effacement. L'alias explicite ne peut
    # pas etre redirige, et c'est celui que le front utilise.
    path("erase", views.AccountDeleteView.as_view(), name="account-erase"),
    path("export", views.AccountExportView.as_view(), name="account-export"),
]
