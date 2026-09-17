from django.urls import re_path

from apps.redirect.views import scan_redirect

app_name = "redirect"

# Le slug n'est pas un `slug` Django (qui autorise `-`/`_` à peu près partout) : on reprend la
# grammaire exacte de apps.common.shortid, pour qu'une adresse mal formée meure avant la base.
urlpatterns = [
    re_path(r"^(?P<slug>[2-9A-HJ-NP-Za-km-z]{4,32})/?$", scan_redirect, name="scan"),
]
