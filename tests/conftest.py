import datetime as dt
import pytest
from decimal import Decimal
from django.test import Client
from django_scopes import scopes_disabled
from freezegun import freeze_time
from pretix.base.models import Event, Organizer, Team, User
from zoneinfo import ZoneInfo

ZAGREB = ZoneInfo("Europe/Zagreb")
NOW = dt.datetime(2026, 11, 17, 10, 0, tzinfo=ZAGREB)


@pytest.fixture
def frozen():
    with freeze_time(NOW) as f:
        yield f


@pytest.fixture
def organizer():
    with scopes_disabled():
        o = Organizer.objects.create(name="SS FER", slug="ssfer", plugins="pretix_bazne_cijene")
        o.settings.bazne_cijene_seller_name = "Savez studenata Fakulteta elektrotehnike i računarstva (SS FER)"
        o.settings.bazne_cijene_seller_address = "Unska ulica 3, Zagreb"
        o.settings.bazne_cijene_oib = "14504100762"
        return o


@pytest.fixture
def event(organizer):
    with scopes_disabled():
        e = Event.objects.create(
            organizer=organizer,
            name="Koncert",
            slug="koncert",
            date_from=dt.datetime(2026, 12, 5, 20, 0, tzinfo=ZAGREB),
            currency="EUR",
            live=True,
        )
        e.settings.timezone = "Europe/Zagreb"
        e.settings.locales = ["hr", "en"]
        e.settings.locale = "hr"
        return e


@pytest.fixture
def quota(event):
    with scopes_disabled():
        return event.quotas.create(name="Kapacitet", size=100)


@pytest.fixture
def item(event, quota):
    with scopes_disabled():
        i = event.items.create(name="Ulaznica", default_price=Decimal("8.00"), admission=True)
        quota.items.add(i)
        return i


@pytest.fixture
def user():
    return User.objects.create_user("admin@ssfer.test", "dummy")


@pytest.fixture
def admin_client(organizer, user):
    with scopes_disabled():
        t = Team.objects.create(
            organizer=organizer,
            name="Admins",
            all_events=True,
            all_organizer_permissions=True,
            all_event_permissions=True,
        )
        t.members.add(user)
    c = Client()
    c.login(email="admin@ssfer.test", password="dummy")
    return c


def refs(event):
    from pretix_bazne_cijene.models import ReferencePrice

    with scopes_disabled():
        return list(ReferencePrice.objects.filter(event=event))


def shop_html(client, event):
    response = client.get(f"/{event.organizer.slug}/{event.slug}/")
    assert response.status_code == 200
    return response.content.decode().replace("\xa0", " ")
