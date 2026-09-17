"""CRUD des QR : propriété, quota, cache invalidé, historique, slug immuable."""

import pytest

pytestmark = pytest.mark.django_db


def test_cree_un_qr_statique_sans_destination(api, auth_api):
    response = auth_api.post(
        "/api/v1/qr/",
        {"kind": "static", "type_id": "text", "label": "Carte wifi", "payload": "WIFI:T:nopass;S:Freebox;;"},
        format="json",
    )
    assert response.status_code == 201, response.data
    body = response.data
    assert body["kind"] == "static"
    assert body["target_url"] == ""
    assert "/r/" in body["short_url"]
    assert len(body["slug"]) == 8


def test_qr_dynamique_exige_une_destination(auth_api):
    response = auth_api.post("/api/v1/qr/", {"kind": "dynamic", "type_id": "url"}, format="json")
    assert response.status_code == 400
    assert "target_url" in str(response.data)


def test_qr_dynamique_refuse_une_destination_interne(auth_api, settings):
    settings.QR = {**settings.QR, "ALLOW_PRIVATE_TARGETS": False}
    response = auth_api.post(
        "/api/v1/qr/", {"kind": "dynamic", "type_id": "url", "target_url": "http://169.254.169.254/"}, format="json"
    )
    assert response.status_code == 400
    assert "interne" in str(response.data)


def test_slug_stable_et_url_courte(auth_api, user):
    from apps.qr.models import QrCode

    qr = QrCode.objects.create(owner=user, kind="dynamic", target_url="https://kamcofarm.com/a")
    reponse = auth_api.patch(f"/api/v1/qr/{qr.pk}/", {"label": "nouveau libellé"}, format="json")
    assert reponse.data["slug"] == qr.slug
    assert reponse.data["short_url"] == f"http://testserver/r/{qr.slug}"


def test_huit_caracteres_base62_sans_ambigus(auth_api, user):
    from apps.qr.models import QrCode

    qr = QrCode.objects.create(owner=user, kind="dynamic", target_url="https://kamcofarm.com/a")
    assert len(qr.slug) == 8
    assert not set(qr.slug) & set("01OIl")  # recopié à la main depuis une facture


def test_modifier_la_destination_ecrit_lhistorique_et_invalide_le_cache(
    auth_api, dynamic_qr, settings, monkeypatch, django_capture_on_commit_callbacks
):
    from apps.qr import cache as qr_cache
    from apps.qr.models import QrVersion

    qr_cache.write(dynamic_qr.slug, {"target_url": "https://ancien.example/"})
    appelee = []
    monkeypatch.setattr(qr_cache, "invalidate", lambda slug: appelee.append(slug))

    # L'invalider *avant* le commit laisserait une requête concurrente recoller l'ancienne valeur en
    # cache pour la durée du TTL : c'est précisément pourquoi `services.update_qr` fait un `on_commit`.
    with django_capture_on_commit_callbacks(execute=True):
        response = auth_api.patch(
            f"/api/v1/qr/{dynamic_qr.pk}/", {"target_url": "https://nouveau.example/x"}, format="json"
        )
    assert response.status_code == 200, response.data
    dynamic_qr.refresh_from_db()
    assert dynamic_qr.target_url == "https://nouveau.example/x"
    assert QrVersion.objects.filter(qr=dynamic_qr, change__has_key="target_url").exists()
    assert appelee == [dynamic_qr.slug]


def test_apres_modification_l_entree_perimee_disparait_reellement(
    auth_api, dynamic_qr, django_capture_on_commit_callbacks
):
    """Le même chemin, sans bouchon : la clé doit être partie, sinon le TTL de 60 s ferait mentir le front."""
    from apps.qr import cache as qr_cache

    qr_cache.write(dynamic_qr.slug, {"target_url": "https://ancien.example/"})
    assert qr_cache.read(dynamic_qr.slug) == {"target_url": "https://ancien.example/"}
    with django_capture_on_commit_callbacks(execute=True):
        assert (
            auth_api.patch(
                f"/api/v1/qr/{dynamic_qr.pk}/", {"target_url": "https://nouveau.example/y"}, format="json"
            ).status_code
            == 200
        )
    assert qr_cache.read(dynamic_qr.slug) is None


def test_un_qr_d_un_tier_est_absent_pour_moi(api, other_user, dynamic_qr):
    """404 et non 403 : un 403 confirmerait que l'identifiant existe."""
    api.force_login(other_user)
    assert api.get(f"/api/v1/qr/{dynamic_qr.pk}/").status_code == 404
    assert api.patch(f"/api/v1/qr/{dynamic_qr.pk}/", {"label": "x"}, format="json").status_code == 404
    assert api.delete(f"/api/v1/qr/{dynamic_qr.pk}/").status_code == 404


def test_suppression_logique_puis_restoration(auth_api, dynamic_qr):
    response = auth_api.delete(f"/api/v1/qr/{dynamic_qr.pk}/")
    assert response.status_code == 204
    dynamic_qr.refresh_from_db()
    assert dynamic_qr.deleted_at is not None and dynamic_qr.is_active is False
    assert dynamic_qr.pk  # la ligne est encore là, pour le flyer déjà imprimé
    assert auth_api.get("/api/v1/qr/").data["results"] == []
    relance = auth_api.post(f"/api/v1/qr/{dynamic_qr.pk}/restore/")
    assert relance.status_code == 200
    dynamic_qr.refresh_from_db()
    assert dynamic_qr.deleted_at is None and dynamic_qr.is_active is True


def test_suppression_dure_efface_aussi_les_stats(auth_api, dynamic_qr):
    from apps.analytics.models import QrDailyStats

    QrDailyStats.objects.create(qr_id=dynamic_qr.pk, day="2026-09-01", country_code="IT", scans=7)
    assert auth_api.delete(f"/api/v1/qr/{dynamic_qr.pk}/?hard=1").status_code == 204
    assert not QrDailyStats.objects.filter(qr_id=dynamic_qr.pk).exists()
    from apps.qr.models import QrCode

    assert not QrCode.objects.filter(pk=dynamic_qr.pk).exists()


def test_pause_et_reprise(auth_api, dynamic_qr):
    assert auth_api.post(f"/api/v1/qr/{dynamic_qr.pk}/pause/").data["is_active"] is False
    dynamic_qr.refresh_from_db()
    assert dynamic_qr.is_resolvable is False
    assert auth_api.post(f"/api/v1/qr/{dynamic_qr.pk}/resume/").data["is_active"] is True


def test_duplication_change_le_slug(auth_api, dynamic_qr):
    clone = auth_api.post(f"/api/v1/qr/{dynamic_qr.pk}/duplicate/")
    assert clone.status_code == 201
    assert clone.data["slug"] != dynamic_qr.slug
    assert clone.data["target_url"] == dynamic_qr.target_url


def test_le_quota_de_qr_dynamiques_compte_les_30_derniers_jours(auth_api, user, monkeypatch):
    """Le plafond est un ** robinet d'infrastructure **, donc il reste réglable sans déployer.

    On baisse la valeur par la variable d'environnement prévue à cet effet (`limite()` la relit a
    chaque appel) plutot qu'en changeant la grille : le test porte sur le comptage glissant et le
    402, pas sur le chiffre commercial.
    """
    monkeypatch.setenv("QUOTA_PREMIUM_DYNAMIQUES_30J", "2")
    for i in range(2):
        response = auth_api.post(
            "/api/v1/qr/", {"kind": "dynamic", "type_id": "url", "target_url": f"https://x.fr/{i}"}, format="json"
        )
        assert response.status_code == 201, response.data
    troisieme = auth_api.post(
        "/api/v1/qr/", {"kind": "dynamic", "type_id": "url", "target_url": "https://x.fr/3"}, format="json"
    )
    assert troisieme.status_code == 402
    assert troisieme.data["error"]["code"] == "quota_exceeded"
    assert troisieme.data["error"]["details"]["limit"] == 2
    assert troisieme.data["error"]["details"]["window_days"] == 30


def test_archiver_un_qr_libere_la_place_du_quota(auth_api, user, monkeypatch):
    """Le quota porte sur les QR **en service** : sans cette echappatoire, un client qui range son
    material d'il y a deux ans ne pourrait plus rien creer — et il aurait raison de le mal prendre."""
    from apps.qr.models import QrCode

    monkeypatch.setenv("QUOTA_PREMIUM_DYNAMIQUES_30J", "2")
    creations = [
        auth_api.post(
            "/api/v1/qr/", {"kind": "dynamic", "type_id": "url", "target_url": f"https://x.fr/{i}"}, format="json"
        )
        for i in range(2)
    ]
    assert all(r.status_code == 201 for r in creations)
    bloque = auth_api.post(
        "/api/v1/qr/", {"kind": "dynamic", "type_id": "url", "target_url": "https://x.fr/2"}, format="json"
    )
    assert bloque.status_code == 402

    from django.utils import timezone

    premier = QrCode.objects.filter(owner=user).order_by("pk").first()
    premier.archived_at = timezone.now()
    premier.save()
    # L'archivage seul ne libere rien (le QR reste imprimable) : c'est la suppression qui compte.
    assert (
        auth_api.post(
            "/api/v1/qr/", {"kind": "dynamic", "type_id": "url", "target_url": "https://x.fr/2"}, format="json"
        ).status_code
        == 402
    )
    auth_api.delete(f"/api/v1/qr/{premier.pk}/?hard=1")
    relance = auth_api.post(
        "/api/v1/qr/", {"kind": "dynamic", "type_id": "url", "target_url": "https://x.fr/3"}, format="json"
    )
    assert relance.status_code == 201, relance.data


def test_contenu_trop_long_pour_un_qr_refuse_des_lecriture(auth_api, settings):
    settings.QR = {**settings.QR, "MAX_PAYLOAD_BYTES": 2953}
    reponse = auth_api.post("/api/v1/qr/", {"kind": "static", "type_id": "text", "payload": "a" * 3000}, format="json")
    assert reponse.status_code == 400
    assert "2953" in str(reponse.data)


def test_design_borne_en_octets_et_en_profondeur(auth_api):
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": {"k": {"l": {"m": {"n": 1}}}}}}}}}}}}}}
    assert (
        auth_api.post("/api/v1/qr/", {"kind": "static", "type_id": "text", "design": deep}, format="json").status_code
        == 400
    )
    huge = {"data": "x" * 40_000}
    reponse = auth_api.post("/api/v1/qr/", {"kind": "static", "type_id": "text", "design": huge}, format="json")
    assert reponse.status_code == 400
    assert "volumineux" in str(reponse.data)


def test_type_inconnu_refuse(auth_api):
    response = auth_api.post("/api/v1/qr/", {"kind": "static", "type_id": "crypto", "payload": "x"}, format="json")
    assert response.status_code == 400
    assert "Type inconnu" in str(response.data)


def test_liste_filtree_et_paginee(auth_api, user):
    from apps.qr.models import QrCode

    for i in range(30):
        QrCode.objects.create(owner=user, kind="static", type_id="text", label=f"lot {i}", payload="x")
    premiere = auth_api.get("/api/v1/qr/?page_size=10")
    assert len(premiere.data["results"]) == 10
    assert premiere.data["next"]
    suivante = auth_api.get(premiere.data["next"])
    ids = {r["id"] for r in suivante.data["results"]}
    assert ids.isdisjoint({r["id"] for r in premiere.data["results"]})
    assert auth_api.get("/api/v1/qr/?search=lot%201").data["results"]


def test_archivage_exclu_de_la_liste_par_defaut(auth_api, dynamic_qr):
    from django.utils import timezone

    dynamic_qr.archived_at = timezone.now()
    dynamic_qr.save()
    assert auth_api.get("/api/v1/qr/").data["results"] == []
    assert len(auth_api.get("/api/v1/qr/?archived=true").data["results"]) == 1


def test_historique_visible_depuis_l_api(auth_api, dynamic_qr):
    auth_api.patch(f"/api/v1/qr/{dynamic_qr.pk}/", {"label": "v2"}, format="json")
    response = auth_api.get(f"/api/v1/qr/{dynamic_qr.pk}/history/")
    assert response.status_code == 200
    assert any("label" in str(item["change"]) or "created" in str(item["change"]) for item in response.data)


def test_put_interdit_on_ne_remplace_pas_un_qr_entier(auth_api, dynamic_qr):
    assert auth_api.put(f"/api/v1/qr/{dynamic_qr.pk}/", {"kind": "static"}, format="json").status_code == 405


def test_stats_vident_les_agregats_et_non_les_lignes_brutes(auth_api, dynamic_qr, settings):
    from datetime import date

    from apps.analytics.models import QrDailyStats

    QrDailyStats.objects.create(qr_id=dynamic_qr.pk, day=date.today(), country_code="IT", scans=12, unique_visitors=9)
    QrDailyStats.objects.create(qr_id=dynamic_qr.pk, day=date.today(), country_code="FR", scans=3, unique_visitors=3)
    dynamic_qr.owner.consent_tracking_at = __import__("django.utils.timezone", fromlist=["now"]).now()
    dynamic_qr.owner.save()
    response = auth_api.get(f"/api/v1/qr/{dynamic_qr.pk}/stats/?days=7")
    assert response.status_code == 200, response.data
    body = response.data
    assert body["countries"][0]["code"] == "IT"
    assert body["countries"][0]["name"] == "Italie"
    assert body["tracking_allowed"] is True  # le propriétaire a consenti

    assert sum(item["scans"] for item in body["series"]) == 15
    # Regression trouvee au smoke test du preview: le total affiche sort des agregats, pas du
    # compteur denormalise. Sans Redis (dev, incident) ou entre deux beat, `scan_count_total` est en
    # retard — le montrer a cote du chiffre exact vaut mieux que de le choisir comme source.
    assert body["total"] == 15
    assert body["total_fenetre"] == 15
    assert body["compteur_temps_reel"] == 0
