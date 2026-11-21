import datetime as dt
import pytest
from decimal import Decimal
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django_scopes import scope, scopes_disabled

from pretix_bazne_cijene.models import ReferencePrice
from pretix_bazne_cijene.pricing import capture, current_price, label_text

from .conftest import refs, shop_html

pytestmark = pytest.mark.django_db


def test_label_is_captured_on_first_render(frozen, event, item):
    html = shop_html(Client(), event)
    assert "Cijena na 17.11.2026.: 8,00 €" in html
    (ref,) = refs(event)
    assert ref.price == Decimal("8.00")
    assert ref.reference_date == dt.date(2026, 11, 17)
    assert ref.source == ReferencePrice.SOURCE_CAPTURE
    with scopes_disabled():
        assert ref.all_logentries().filter(action_type="pretix_bazne_cijene.reference.captured").exists()


def test_reference_survives_price_change(frozen, event, item):
    shop_html(Client(), event)
    frozen.tick(dt.timedelta(days=3))
    with scopes_disabled():
        item.default_price = Decimal("12.00")
        item.save()
    html = shop_html(Client(), event)
    assert "12,00" in html
    assert "Cijena na 17.11.2026.: 8,00 €" in html
    assert len(refs(event)) == 1


def test_nothing_is_captured_while_not_live(frozen, event, item, admin_client):
    with scopes_disabled():
        event.live = False
        event.save()
    html = shop_html(admin_client, event)
    assert 'class="bazne-cijene-label"' in html
    assert refs(event) == []


def test_nothing_is_captured_in_test_mode(frozen, event, item):
    with scopes_disabled():
        event.testmode = True
        event.save()
    shop_html(Client(), event)
    assert refs(event) == []


def test_nothing_happens_when_plugin_disabled(frozen, organizer, event, item):
    with scopes_disabled():
        organizer.plugins = ""
        organizer.save()
    html = shop_html(Client(), event)
    assert "bazne-cijene-label" not in html
    assert "pretix_bazne_cijene/label.css" not in html
    assert refs(event) == []


def test_reference_includes_taxes(frozen, event, item):
    with scopes_disabled():
        rule = event.tax_rules.create(name="PDV", rate=Decimal("25.00"), price_includes_tax=False)
        item.tax_rule = rule
        item.save()
        with scope(organizer=event.organizer):
            assert current_price(event, item).retail == Decimal("10.00")


def test_label_custom_template_and_fallback(event):
    event.organizer.settings.bazne_cijene_label = "Sidrena cijena ({date}): {price}"
    assert label_text(event, Decimal("8"), dt.date(2026, 11, 17), "EUR") == "Sidrena cijena (17.11.2026.): 8,00\xa0€"
    for broken in ("Broken {nope}", "Godina {date.year}", "{price[0]}", "{price!r}", "{price:>9}", "{"):
        event.organizer.settings.bazne_cijene_label = broken
        assert label_text(event, Decimal("8"), dt.date(2026, 11, 17), "EUR") == "Cijena na 17.11.2026.: 8,00\xa0€"


def test_capture_twice_returns_existing(frozen, event, item):
    # The second create hits the unique constraint, as a parallel request would.
    with scope(organizer=event.organizer):
        first = capture(event, item)
        second = capture(event, item)
    assert first.pk == second.pk
    assert len(refs(event)) == 1


def test_shop_page_reads_base_prices_with_one_query(frozen, event, item, quota):
    with scopes_disabled():
        for i in range(9):
            quota.items.add(event.items.create(name=f"Ulaznica {i}", default_price=Decimal("5.00")))
    shop_html(Client(), event)
    assert len(refs(event)) == 10

    with CaptureQueriesContext(connection) as ctx:
        html = shop_html(Client(), event)
    assert html.count('class="bazne-cijene-label"') == 10
    queries = [q["sql"] for q in ctx.captured_queries if "pretix_bazne_cijene_referenceprice" in q["sql"]]
    assert len(queries) == 1


def test_sold_out_tickets_hidden_by_the_shop_are_not_captured(frozen, event, item):
    with scopes_disabled():
        gone_quota = event.quotas.create(name="Rasprodano", size=0)
        gone = event.items.create(name="Rasprodana", default_price=Decimal("3.00"))
        gone_quota.items.add(gone)
        no_quota = event.items.create(name="Bez kvote", default_price=Decimal("3.00"))
    event.settings.hide_sold_out = True
    shop_html(Client(), event)
    assert {r.item_id for r in refs(event)} == {item.pk}

    event.settings.hide_sold_out = False
    shop_html(Client(), event)
    assert {r.item_id for r in refs(event)} == {item.pk, gone.pk}
    assert no_quota.pk not in {r.item_id for r in refs(event)}
