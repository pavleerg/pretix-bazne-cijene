"""Pravilnik (NN 132/2026) čl. 3 (reference day, price before discount), čl. 4 (exemptions) and the label."""
import datetime as dt
import pytest
from decimal import Decimal
from django.test import Client
from django_scopes import scope, scopes_disabled
from freezegun import freeze_time

from pretix_bazne_cijene.models import ReferencePrice
from pretix_bazne_cijene.pricing import current_price

from .conftest import ZAGREB, refs, shop_html

pytestmark = pytest.mark.django_db


def _at(*args):
    return dt.datetime(*args, tzinfo=ZAGREB)


# čl. 3: reference day


def test_shop_offered_before_base_day_shows_price_of_10_september(event, item):
    with freeze_time(_at(2026, 9, 1, 10)) as frozen:
        html = shop_html(Client(), event)
        assert "Cijena na 10.9.2026.: 8,00 €" in html
        assert refs(event) == []

        frozen.move_to(_at(2026, 9, 10, 9))
        with scopes_disabled():
            item.default_price = Decimal("9.00")
            item.save()
        html = shop_html(Client(), event)
        assert "Cijena na 10.9.2026.: 9,00 €" in html
        (ref,) = refs(event)
        assert (ref.price, ref.reference_date) == (Decimal("9.00"), dt.date(2026, 9, 10))

        frozen.move_to(_at(2026, 11, 17, 10))
        with scopes_disabled():
            item.default_price = Decimal("12.00")
            item.save()
        html = shop_html(Client(), event)
        assert "12,00 €" in html
        assert "Cijena na 10.9.2026.: 9,00 €" in html


# čl. 3: discount on the reference day


def test_shop_discount_on_reference_day_shows_price_before_discount(frozen, event, item):
    with scopes_disabled():
        item.original_price = Decimal("10.00")
        item.save()
        with scope(organizer=event.organizer):
            p = current_price(event, item)
    assert (p.retail, p.regular, p.special_sale) == (Decimal("8.00"), Decimal("10.00"), True)
    html = shop_html(Client(), event)
    assert "Cijena na 17.11.2026.: 10,00 €" in html
    (ref,) = refs(event)
    assert ref.price == Decimal("10.00")


def test_variations_and_dates_get_separate_references(frozen, event, item):
    with scopes_disabled():
        event.has_subevents = True
        event.save()
        student = item.variations.create(value="Studenti", default_price=Decimal("5.00"))
        regular = item.variations.create(value="Redovna")
        se1 = event.subevents.create(name="Dan 1", active=True, date_from=_at(2026, 12, 5, 20))
        se2 = event.subevents.create(name="Dan 2", active=True, date_from=_at(2026, 12, 6, 20))
        for se in (se1, se2):
            q = event.quotas.create(name=f"q{se.pk}", size=10, subevent=se)
            q.items.add(item)
            q.variations.add(student, regular)
        se2.subeventitemvariation_set.create(variation=regular, price=Decimal("10.00"))
    c = Client()
    html1 = c.get(f"/ssfer/koncert/{se1.pk}/").content.decode().replace("\xa0", " ")
    html2 = c.get(f"/ssfer/koncert/{se2.pk}/").content.decode().replace("\xa0", " ")
    assert "Cijena na 17.11.2026.: 8,00 €" in html1
    assert "Cijena na 17.11.2026.: 10,00 €" in html2
    by_key = {(r.subevent_id, r.variation_id): r.price for r in refs(event)}
    assert by_key == {
        (se1.pk, student.pk): Decimal("5.00"),
        (se1.pk, regular.pk): Decimal("8.00"),
        (se2.pk, student.pk): Decimal("5.00"),
        (se2.pk, regular.pk): Decimal("10.00"),
    }


def test_label_in_croatian_next_to_the_price(frozen, event, item, quota):
    with scopes_disabled():
        variation_item = event.items.create(name="Paket", default_price=Decimal("20.00"))
        quota.items.add(variation_item)
        vip = variation_item.variations.create(value="VIP", default_price=Decimal("25.00"))
        quota.variations.add(vip)
    html = shop_html(Client(), event)
    assert '<span class="bazne-cijene-label">Cijena na 17.11.2026.: 8,00 €</span>' in html
    assert '<span class="bazne-cijene-label">Cijena na 17.11.2026.: 25,00 €</span>' in html
    assert "pretix_bazne_cijene/label.css" in html


def test_label_is_croatian_in_an_english_shop(frozen, event, item):
    event.settings.locales = ["en"]
    event.settings.locale = "en"
    html = shop_html(Client(), event)
    assert '<span class="bazne-cijene-label">Cijena na 17.11.2026.: 8,00 €</span>' in html


# čl. 4: exemptions


def test_voucher_only_items_show_no_label(frozen, event, item, quota):
    with scopes_disabled():
        hidden = event.items.create(name="Tajna", default_price=Decimal("3.00"), hide_without_voucher=True)
        coded = event.items.create(name="Samo s kodom", default_price=Decimal("4.00"), require_voucher=True)
        quota.items.add(hidden, coded)
        event.vouchers.create(code="TAJNA123", item=hidden)
    html = shop_html(Client(), event)
    assert "Samo s kodom" in html
    assert html.count('class="bazne-cijene-label"') == 1

    r = Client().get("/ssfer/koncert/redeem?voucher=TAJNA123")
    assert r.status_code == 200
    html = r.content.decode()
    assert "Tajna" in html
    assert "bazne-cijene-label" not in html
    with scopes_disabled():
        assert not ReferencePrice.objects.filter(item__in=[hidden, coded]).exists()
