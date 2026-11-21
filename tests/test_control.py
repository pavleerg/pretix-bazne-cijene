import datetime as dt
import pytest
from decimal import Decimal
from django.test import Client
from django_scopes import scope, scopes_disabled
from pretix.base.signals import event_live_issues

from pretix_bazne_cijene.models import ReferencePrice
from pretix_bazne_cijene.pricing import capture

pytestmark = pytest.mark.django_db


def test_index_lists_references(frozen, admin_client, event, item):
    with scope(organizer=event.organizer):
        capture(event, item)
    r = admin_client.get("/control/event/ssfer/koncert/bazne-cijene/")
    assert r.status_code == 200
    assert "Ulaznica" in r.content.decode()


def test_correction_requires_reason_and_is_logged(frozen, admin_client, user, event, item):
    with scope(organizer=event.organizer):
        ref = capture(event, item)
    url = f"/control/event/ssfer/koncert/bazne-cijene/{ref.pk}/"
    assert admin_client.get(url).status_code == 200

    r = admin_client.post(url, {"price": "7.00", "reference_date": "2026-11-16", "reason": ""})
    assert r.status_code == 200
    r = admin_client.post(
        url, {"price": "7.00", "reference_date": "2026-11-16", "reason": "Pogrešna cijena na 5 minuta."}
    )
    assert r.status_code == 302
    with scopes_disabled():
        ref.refresh_from_db()
        assert ref.price == Decimal("7.00")
        assert ref.reference_date == dt.date(2026, 11, 16)
        assert ref.source == ReferencePrice.SOURCE_MANUAL
        assert "Pogrešna cijena" in ref.note
        log = ref.all_logentries().get(action_type="pretix_bazne_cijene.reference.changed")
        assert log.user == user
        assert log.parsed_data["old_price"] == "8.00"


def test_correction_rejects_future_date(frozen, admin_client, event, item):
    with scope(organizer=event.organizer):
        ref = capture(event, item)
    r = admin_client.post(
        f"/control/event/ssfer/koncert/bazne-cijene/{ref.pk}/",
        {"price": "7.00", "reference_date": "2027-01-01", "reason": "x"},
    )
    assert r.status_code == 200
    with scopes_disabled():
        ref.refresh_from_db()
    assert ref.price == Decimal("8.00")


def test_correction_needs_permission(frozen, event, item):
    from pretix.base.models import Team, User

    with scope(organizer=event.organizer):
        ref = capture(event, item)
    u = User.objects.create_user("viewer@ssfer.test", "dummy")
    with scopes_disabled():
        t = Team.objects.create(organizer=event.organizer, name="Viewers", all_events=True)
        t.members.add(u)
    c = Client()
    c.login(email="viewer@ssfer.test", password="dummy")
    assert c.get("/control/event/ssfer/koncert/bazne-cijene/").status_code == 200
    assert c.get(f"/control/event/ssfer/koncert/bazne-cijene/{ref.pk}/").status_code == 403


def test_organizer_settings(frozen, admin_client, organizer):
    url = "/control/organizer/ssfer/bazne-cijene/"
    r = admin_client.get(url)
    assert r.status_code == 200
    assert "/ssfer/cijene/cjenik.csv" in r.content.decode()
    data = {
        "bazne_cijene_label": "Sidrena cijena {date}: {price}",
        "bazne_cijene_pricelist_enabled": "on",
        "bazne_cijene_seller_name": "Savez studenata Fakulteta elektrotehnike i računarstva (SS FER)",
        "bazne_cijene_seller_address": "Unska ulica 3, Zagreb",
        "bazne_cijene_oib": "14504100762",
        "bazne_cijene_pricelist_seq_offset": "1",
        "bazne_cijene_notify_email": "cjenik@kset.test",
    }
    r = admin_client.post(url, data)
    assert r.status_code == 302
    organizer.settings.flush()
    assert organizer.settings.bazne_cijene_oib == "14504100762"
    assert organizer.settings.bazne_cijene_pricelist_seq_offset == 1
    assert organizer.settings.bazne_cijene_notify_email == "cjenik@kset.test"

    for broken in ("Cijena {datum}", "Godina {date.year}", "{price[0]}", "{"):
        data["bazne_cijene_label"] = broken
        r = admin_client.post(url, data)
        assert r.status_code == 200
        assert "Only the placeholders" in r.content.decode()

    data["bazne_cijene_label"] = "Cijena na {date}: {price}"
    data["bazne_cijene_oib"] = "12345"
    r = admin_client.post(url, data)
    assert r.status_code == 200
    assert "11 digits" in r.content.decode()

    data["bazne_cijene_oib"] = "14504100762"
    data["bazne_cijene_notify_email"] = "nije-email"
    r = admin_client.post(url, data)
    assert r.status_code == 200
    assert "valid email" in r.content.decode()


def test_live_issues(event):
    def issues():
        return [str(resp) for recv, resp in event_live_issues.send(event) if resp]

    assert issues() == []
    with scopes_disabled():
        event.currency = "USD"
        event.save()
    event.settings.display_net_prices = True
    (text,) = issues()
    assert "USD" in text and "net prices" in text


def test_rebuild_now_button(frozen, admin_client, organizer, event, item, django_capture_on_commit_callbacks, mailoutbox):
    from pretix_bazne_cijene.models import PriceListVersion

    settings_url = "/control/organizer/ssfer/bazne-cijene/"
    url = "/control/organizer/ssfer/bazne-cijene/rebuild/"
    assert "Rebuild the price list now" in admin_client.get(settings_url).content.decode()
    assert admin_client.get(url).status_code == 405
    organizer.settings.bazne_cijene_notify_email = "cjenik@kset.test"

    with django_capture_on_commit_callbacks(execute=True):
        r = admin_client.post(url, follow=True)
    assert "New price list published: 14504100762_ZA_SVA_PRODAJNA_MJESTA_USLUGE_001_20261117_0300.csv" in r.content.decode()
    assert len(mailoutbox) == 1

    with django_capture_on_commit_callbacks(execute=True):
        r = admin_client.post(url, follow=True)
    assert "Nothing changed, the current price list stays" in r.content.decode()
    assert len(mailoutbox) == 1

    with scopes_disabled():
        item.default_price = Decimal("9.00")
        item.save()
    with django_capture_on_commit_callbacks(execute=True):
        r = admin_client.post(url, follow=True)
    assert "_002_20261117_0300.csv" in r.content.decode()
    assert len(mailoutbox) == 2
    with scopes_disabled():
        assert PriceListVersion.objects.count() == 2
    assert organizer.settings.bazne_cijene_pricelist_checked == ""

    organizer.settings.bazne_cijene_oib = ""
    r = admin_client.post(url, follow=True)
    assert "Enter the seller OIB first" in r.content.decode()


def test_saving_settings_publishes_the_first_price_list(
    frozen, admin_client, organizer, event, item, django_capture_on_commit_callbacks
):
    from pretix_bazne_cijene.models import PriceListVersion

    organizer.settings.bazne_cijene_oib = ""
    data = {
        "bazne_cijene_label": "Cijena na {date}: {price}",
        "bazne_cijene_pricelist_enabled": "on",
        "bazne_cijene_oib": "",
    }
    assert admin_client.post("/control/organizer/ssfer/bazne-cijene/", data).status_code == 302
    with scopes_disabled():
        assert PriceListVersion.objects.count() == 0

    data["bazne_cijene_oib"] = "14504100762"
    with django_capture_on_commit_callbacks(execute=True):
        assert admin_client.post("/control/organizer/ssfer/bazne-cijene/", data).status_code == 302
    assert Client().get("/ssfer/cijene/cjenik.csv").status_code == 200

    data["bazne_cijene_seller_name"] = "SS FER"
    with django_capture_on_commit_callbacks(execute=True):
        assert admin_client.post("/control/organizer/ssfer/bazne-cijene/", data).status_code == 302
    with scopes_disabled():
        assert PriceListVersion.objects.count() == 1


def test_migrations_match_the_models(settings):
    from django.core.management import call_command

    # pretix' test settings disable migrations; load them for this check.
    settings.MIGRATION_MODULES = {}
    call_command("makemigrations", "pretix_bazne_cijene", "--check", "--dry-run", verbosity=0)
