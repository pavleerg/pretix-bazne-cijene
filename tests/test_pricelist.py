import csv
import datetime as dt
import io
import pytest
from decimal import Decimal
from django.db import IntegrityError, transaction
from django.test import Client
from django_scopes import scope, scopes_disabled
from freezegun import freeze_time

from pretix_bazne_cijene.models import PriceListVersion
from pretix_bazne_cijene.pricelist import COLUMNS, filename, refresh

from .conftest import ZAGREB

pytestmark = pytest.mark.django_db

HEADER = "Naziv usluge;Maloprodajna cijena;Posebni oblik prodaje (DA/NE);Bazna cijena"


def rows_of(version):
    return list(csv.DictReader(io.StringIO(version.csv), delimiter=";"))


def do_refresh(organizer):
    with scope(organizer=organizer):
        return refresh(organizer)


def test_csv_content(frozen, organizer, event, item):
    version = do_refresh(organizer)
    assert version.csv.splitlines()[0] == HEADER == ";".join(COLUMNS)
    assert version.csv.splitlines()[1] == "Ulaznica;8,00;NE;8,00"
    (row,) = rows_of(version)
    assert row == {
        "Naziv usluge": "Ulaznica",
        "Maloprodajna cijena": "8,00",
        "Posebni oblik prodaje (DA/NE)": "NE",
        "Bazna cijena": "8,00",
    }


def test_rows_use_the_public_name_without_internal_name(frozen, organizer, event, item):
    for internal_name in (None, "", "   "):
        with scopes_disabled():
            item.internal_name = internal_name
            item.save()
        frozen.tick(dt.timedelta(minutes=1))
        (row,) = rows_of(do_refresh(organizer))
        assert row["Naziv usluge"] == "Ulaznica"


def test_dates_of_a_series_use_the_internal_name(frozen, organizer, event, item):
    with scopes_disabled():
        event.has_subevents = True
        event.save()
        item.internal_name = "  Koncert - regular  "
        item.save()
        v = item.variations.create(value="Studenti", default_price=Decimal("5.00"))
        for day in (5, 6):
            se = event.subevents.create(name=f"Dan {day}", active=True, date_from=dt.datetime(2026, 12, day, 20, tzinfo=ZAGREB))
            q = event.quotas.create(name=f"q{day}", size=10, subevent=se)
            q.items.add(item)
            q.variations.add(v)
    names = [r["Naziv usluge"] for r in rows_of(do_refresh(organizer))]
    assert names == ["Koncert - regular", "Koncert - regular"]


def test_voucher_only_tickets_get_no_base_price(frozen, organizer, event, item, quota):
    from pretix_bazne_cijene.pricing import lookup

    with scopes_disabled():
        coded = event.items.create(name="Samo s kodom", default_price=Decimal("4.00"), require_voucher=True)
        quota.items.add(coded)
    do_refresh(organizer)
    with scope(organizer=organizer):
        assert lookup(event, coded) is None
        assert lookup(event, item) is not None


def test_version_numbers_are_unique(frozen, organizer, event, item):
    v1 = do_refresh(organizer)
    with scopes_disabled(), pytest.raises(IntegrityError), transaction.atomic():
        PriceListVersion.objects.create(organizer=organizer, created=v1.created, seq=v1.seq, content_hash="x", csv="")


def test_rows_use_internal_name(frozen, organizer, event, item):
    with scopes_disabled():
        item.internal_name = 'Idu Hobiti – promocija albuma "Idu Dalje" + gosti - regular'
        item.save()
    version = do_refresh(organizer)
    assert version.csv.splitlines()[1] == '"Idu Hobiti – promocija albuma ""Idu Dalje"" + gosti - regular";8,00;NE;8,00'


def test_price_change_creates_new_version_with_old_base_price(frozen, organizer, event, item):
    v1 = do_refresh(organizer)
    assert do_refresh(organizer).pk == v1.pk
    frozen.tick(dt.timedelta(hours=2))
    with scopes_disabled():
        item.default_price = Decimal("12.00")
        item.save()
    v2 = do_refresh(organizer)
    assert v2.pk != v1.pk
    (row,) = rows_of(v2)
    assert (row["Maloprodajna cijena"], row["Bazna cijena"]) == ("12,00", "8,00")


def test_special_sale_is_flagged(frozen, organizer, event, item):
    with scopes_disabled():
        item.original_price = Decimal("10.00")
        item.save()
    (row,) = rows_of(do_refresh(organizer))
    assert row["Maloprodajna cijena"] == "8,00"
    assert row["Posebni oblik prodaje (DA/NE)"] == "DA"
    assert row["Bazna cijena"] == "10,00"


def test_only_publicly_offered_tickets_are_listed(frozen, organizer, event, item, quota):
    with scopes_disabled():
        hidden = event.items.create(name="Tajna", default_price=Decimal("1.00"), hide_without_voucher=True)
        inactive = event.items.create(name="Neaktivna", default_price=Decimal("1.00"), active=False)
        later = event.items.create(
            name="Kasnije", default_price=Decimal("1.00"), available_from=dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)
        )
        quota.items.add(hidden, inactive, later)
        offline = event.organizer.events.create(
            name="Offline", slug="offline", date_from=event.date_from, live=False, currency="EUR"
        )
        oq = offline.quotas.create(name="q", size=1)
        oq.items.add(offline.items.create(name="X", default_price=Decimal("1.00")))
        past = event.organizer.events.create(
            name="Prošlo", slug="proslo", date_from=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
            live=True, currency="EUR"
        )
        pq = past.quotas.create(name="q", size=1)
        pq.items.add(past.items.create(name="Y", default_price=Decimal("1.00")))
    names = [r["Naziv usluge"] for r in rows_of(do_refresh(organizer))]
    assert names == ["Ulaznica"]


def test_variations_use_the_ticket_name(frozen, organizer, event, item, quota):
    with scopes_disabled():
        item.internal_name = "Koncert - regular"
        item.save()
        v = item.variations.create(value="Studenti", default_price=Decimal("0.00"))
        quota.variations.add(v)
    (row,) = rows_of(do_refresh(organizer))
    assert row["Naziv usluge"] == "Koncert - regular"
    assert row["Maloprodajna cijena"] == "0,00"
    assert row["Bazna cijena"] == "0,00"


def test_file_name_na_dan_is_the_same_day(organizer, event, item):
    with freeze_time(dt.datetime(2026, 11, 30, 23, 30, tzinfo=ZAGREB)):
        version = do_refresh(organizer)
    assert filename(organizer, version) == "14504100762_ZA_SVA_PRODAJNA_MJESTA_USLUGE_001_20261130_0300.csv"


def test_numbering_continues_after_the_manual_lists(frozen, organizer, event, item):
    organizer.settings.bazne_cijene_pricelist_seq_offset = 1
    v1 = do_refresh(organizer)
    with scopes_disabled():
        item.default_price = Decimal("9.00")
        item.save()
    v2 = do_refresh(organizer)
    assert (v1.seq, v2.seq) == (2, 3)
    assert filename(organizer, v2) == "14504100762_ZA_SVA_PRODAJNA_MJESTA_USLUGE_003_20261117_0300.csv"


def test_old_versions_are_kept_15_days_after_replacement(frozen, organizer, event, item):
    with scopes_disabled():
        event.date_from = dt.datetime(2027, 6, 1, 20, tzinfo=dt.timezone.utc)
        event.save()
    v1 = do_refresh(organizer)
    frozen.tick(dt.timedelta(days=1))
    with scopes_disabled():
        item.default_price = Decimal("9.00")
        item.save()
    v2 = do_refresh(organizer)
    frozen.tick(dt.timedelta(days=14))
    do_refresh(organizer)
    with scopes_disabled():
        assert set(PriceListVersion.objects.values_list("pk", flat=True)) == {v1.pk, v2.pk}
    frozen.tick(dt.timedelta(days=1, hours=1))
    do_refresh(organizer)
    with scopes_disabled():
        assert set(PriceListVersion.objects.values_list("pk", flat=True)) == {v2.pk}
    frozen.tick(dt.timedelta(days=60))
    do_refresh(organizer)
    with scopes_disabled():
        assert set(PriceListVersion.objects.values_list("pk", flat=True)) == {v2.pk}


def test_public_endpoints_404_before_the_first_rebuild(frozen, organizer, event, item):
    assert Client().get("/ssfer/cijene/cjenik.csv").status_code == 404
    assert Client().get("/ssfer/cijene/").status_code == 404
    with scopes_disabled():
        assert PriceListVersion.objects.count() == 0


def test_public_csv_endpoint(frozen, organizer, event, item):
    do_refresh(organizer)
    r = Client().get("/ssfer/cijene/cjenik.csv")
    assert r.status_code == 200
    assert r["Content-Type"] == "text/csv; charset=utf-8"
    assert r["Access-Control-Allow-Origin"] == "*"
    assert r["Content-Disposition"] == (
        'inline; filename="14504100762_ZA_SVA_PRODAJNA_MJESTA_USLUGE_001_20261117_0300.csv"'
    )
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert r.content.decode("utf-8-sig") == HEADER + "\nUlaznica;8,00;NE;8,00\n"
    assert Client().get("/ssfer/cijene/cjenik.xml").status_code == 404


def test_public_page_and_archive(frozen, organizer, event, item):
    c = Client()
    do_refresh(organizer)
    assert c.get("/ssfer/cijene/").status_code == 200
    frozen.tick(dt.timedelta(hours=3))
    with scopes_disabled():
        item.default_price = Decimal("9.00")
        item.save()
    do_refresh(organizer)
    r = c.get("/ssfer/cijene/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "<title>Cjenik usluga – SS FER</title>" in html
    assert (
        "Savez studenata Fakulteta elektrotehnike i računarstva (SS FER), Unska ulica 3, Zagreb, "
        "OIB 14504100762" in html
    )
    assert "Važeći cjenik" in html
    assert "Arhiva (prethodni cjenici, 15 dana)" in html
    assert ">14504100762_ZA_SVA_PRODAJNA_MJESTA_USLUGE_002_20261117_0300.csv</a>" in html
    with scopes_disabled():
        old = PriceListVersion.objects.get(seq=1)
    assert (
        f'<a href="/ssfer/cijene/arhiva/{old.pk}.csv">'
        "14504100762_ZA_SVA_PRODAJNA_MJESTA_USLUGE_001_20261117_0300.csv</a> (zamijenjen 17.11.2026. u 13:00)"
    ) in html
    r = c.get(f"/ssfer/cijene/arhiva/{old.pk}.csv")
    assert r.status_code == 200
    assert "Ulaznica;8,00;NE;8,00" in r.content.decode("utf-8-sig")


def test_public_endpoint_404_when_disabled(frozen, organizer, event, item):
    do_refresh(organizer)
    organizer.settings.bazne_cijene_pricelist_enabled = False
    assert Client().get("/ssfer/cijene/cjenik.csv").status_code == 404
    organizer.settings.bazne_cijene_pricelist_enabled = True
    organizer.settings.bazne_cijene_oib = ""
    assert Client().get("/ssfer/cijene/cjenik.csv").status_code == 404
    organizer.settings.bazne_cijene_oib = "14504100762"
    with scopes_disabled():
        organizer.plugins = ""
        organizer.save()
    assert Client().get("/ssfer/cijene/cjenik.csv").status_code == 404


def test_saving_or_opening_does_not_rebuild(frozen, organizer, event, item, django_capture_on_commit_callbacks):
    c = Client()
    do_refresh(organizer)
    assert "Ulaznica;8,00" in c.get("/ssfer/cijene/cjenik.csv").content.decode("utf-8-sig")
    with django_capture_on_commit_callbacks(execute=True):
        with scopes_disabled():
            item.default_price = Decimal("15.00")
            item.save()
    frozen.tick(dt.timedelta(hours=1))
    assert "Ulaznica;8,00" in c.get("/ssfer/cijene/cjenik.csv").content.decode("utf-8-sig")
    assert c.get("/ssfer/cijene/").status_code == 200
    with scopes_disabled():
        assert PriceListVersion.objects.count() == 1


def run_periodic():
    from pretix_bazne_cijene.signals import periodic_refresh

    periodic_refresh(None)


def versions(organizer):
    with scopes_disabled():
        return list(PriceListVersion.objects.filter(organizer=organizer).order_by("seq"))


def test_daily_rebuild_at_3(organizer, event, item):
    with freeze_time(dt.datetime(2026, 11, 17, 2, 50, tzinfo=ZAGREB)) as frozen:
        run_periodic()
        assert versions(organizer) == []
        frozen.move_to(dt.datetime(2026, 11, 17, 3, 0, 30, tzinfo=ZAGREB))
        run_periodic()
        (v1,) = versions(organizer)
        assert v1.created == dt.datetime(2026, 11, 17, 3, 0, 30, tzinfo=ZAGREB)
        assert filename(organizer, v1) == "14504100762_ZA_SVA_PRODAJNA_MJESTA_USLUGE_001_20261117_0300.csv"

        frozen.move_to(dt.datetime(2026, 11, 17, 14, 0, tzinfo=ZAGREB))
        with scopes_disabled():
            item.default_price = Decimal("9.00")
            item.save()
        run_periodic()
        frozen.move_to(dt.datetime(2026, 11, 18, 2, 59, tzinfo=ZAGREB))
        run_periodic()
        assert versions(organizer) == [v1]

        frozen.move_to(dt.datetime(2026, 11, 18, 3, 5, tzinfo=ZAGREB))
        run_periodic()
        v1_, v2 = versions(organizer)
        assert rows_of(v2)[0]["Maloprodajna cijena"] == "9,00"
        assert filename(organizer, v2) == "14504100762_ZA_SVA_PRODAJNA_MJESTA_USLUGE_002_20261118_0300.csv"

        frozen.move_to(dt.datetime(2026, 11, 19, 3, 5, tzinfo=ZAGREB))
        run_periodic()
        assert versions(organizer) == [v1, v2]


def test_daily_rebuild_catches_up_when_cron_was_down_at_3(organizer, event, item):
    with freeze_time(dt.datetime(2026, 11, 17, 11, 0, tzinfo=ZAGREB)) as frozen:
        run_periodic()
        assert len(versions(organizer)) == 1
        with scopes_disabled():
            item.default_price = Decimal("9.00")
            item.save()
        frozen.move_to(dt.datetime(2026, 11, 17, 23, 0, tzinfo=ZAGREB))
        run_periodic()
        assert len(versions(organizer)) == 1


def test_daily_rebuild_skips_disabled_price_list(organizer, event, item):
    organizer.settings.bazne_cijene_pricelist_enabled = False
    with freeze_time(dt.datetime(2026, 11, 17, 7, 0, tzinfo=ZAGREB)):
        run_periodic()
    assert versions(organizer) == []


def test_daily_rebuild_waits_for_the_oib(organizer, event, item):
    organizer.settings.bazne_cijene_oib = ""
    organizer.settings.bazne_cijene_notify_email = "cjenik@kset.test"
    with freeze_time(dt.datetime(2026, 11, 17, 7, 0, tzinfo=ZAGREB)):
        run_periodic()
    assert versions(organizer) == []
    assert organizer.settings.bazne_cijene_pricelist_checked == ""


def test_failed_daily_rebuild_is_retried_after_an_hour(organizer, event, item, monkeypatch):
    from pretix_bazne_cijene import pricelist

    calls = []

    def broken(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("boom")

    with freeze_time(dt.datetime(2026, 11, 17, 3, 5, tzinfo=ZAGREB)) as frozen:
        with monkeypatch.context() as m:
            m.setattr(pricelist, "offered_rows", broken)
            run_periodic()
            assert len(calls) == 1
            frozen.tick(dt.timedelta(minutes=10))
            run_periodic()
            assert len(calls) == 1
        assert versions(organizer) == []
        organizer.settings.flush()
        assert organizer.settings.bazne_cijene_pricelist_checked == ""

        frozen.tick(dt.timedelta(minutes=51))
        run_periodic()
        assert len(versions(organizer)) == 1


def at(day, hour, minute=0):
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=ZAGREB)


def names_at(organizer, moment):
    with freeze_time(moment):
        return [r["Naziv usluge"] for r in rows_of(do_refresh(organizer))]


def test_list_covers_the_whole_day(organizer, event, item, quota):
    day = dt.date(2026, 11, 17)
    tomorrow = day + dt.timedelta(days=1)
    with scopes_disabled():
        later_today = event.items.create(name="Od 11", default_price=Decimal("5.00"), available_from=at(day, 11))
        short = event.items.create(
            name="11-12", default_price=Decimal("5.00"), available_from=at(day, 11), available_until=at(day, 12)
        )
        ends_tonight = event.items.create(name="Do 20", default_price=Decimal("5.00"), available_until=at(day, 20))
        ended_at_night = event.items.create(name="Do 2", default_price=Decimal("5.00"), available_until=at(day, 2))
        after_midnight = event.items.create(
            name="Od ponoći", default_price=Decimal("5.00"), available_from=at(tomorrow, 0, 30)
        )
        quota.items.add(later_today, short, ends_tonight, ended_at_night, after_midnight)
        noon = event.organizer.events.create(
            name="Podne", slug="podne", date_from=at(day + dt.timedelta(days=10), 20), presale_start=at(day, 12),
            live=True, currency="EUR",
        )
        nq = noon.quotas.create(name="q", size=10)
        nq.items.add(noon.items.create(name="Prodaja od podne", default_price=Decimal("7.00")))

    # Shop order, except that tickets going on sale later in the day come last.
    assert names_at(organizer, at(day, 3)) == ["Prodaja od podne", "Do 20", "Ulaznica", "11-12", "Od 11"]
    assert names_at(organizer, at(tomorrow, 3)) == ["Prodaja od podne", "Od 11", "Od ponoći", "Ulaznica"]


def test_base_price_of_later_tickets_is_captured_at_the_real_time(organizer, event, item, quota):
    from pretix_bazne_cijene.pricing import lookup

    six = at(dt.date(2026, 11, 17), 6)
    with scopes_disabled():
        later = event.items.create(name="Od 11", default_price=Decimal("5.00"), available_from=six + dt.timedelta(hours=5))
        quota.items.add(later)
    with freeze_time(six):
        (row,) = [r for r in rows_of(do_refresh(organizer)) if r["Naziv usluge"] == "Od 11"]
    assert row["Bazna cijena"] == "5,00"
    with scope(organizer=organizer):
        ref = lookup(event, later)
    assert ref.reference_date == dt.date(2026, 11, 17)
    assert ref.captured_at == six


def test_email_when_a_new_list_is_published(organizer, event, item, django_capture_on_commit_callbacks, mailoutbox):
    organizer.settings.bazne_cijene_notify_email = "cjenik@kset.test"
    with freeze_time(at(dt.date(2026, 11, 17), 3)):
        with django_capture_on_commit_callbacks(execute=True):
            do_refresh(organizer)
    (first,) = mailoutbox
    assert first.to == ["cjenik@kset.test"]
    assert first.subject == "Cjenik usluga osvježen: 14504100762_ZA_SVA_PRODAJNA_MJESTA_USLUGE_001_20261117_0300.csv"
    assert "Trenutno stanje (broj usluga: 1):\n" + HEADER + "\nUlaznica;8,00;NE;8,00\n" in first.body
    assert "Ovo je prvi cjenik" in first.body
    assert "+ Ulaznica" not in first.body
    assert "/ssfer/cijene/cjenik.csv" in first.body

    with scopes_disabled():
        item.internal_name = 'Idu Hobiti "Idu Dalje" - regular'
        item.default_price = Decimal("9.00")
        item.save()
    with freeze_time(at(dt.date(2026, 11, 18), 3)):
        with django_capture_on_commit_callbacks(execute=True):
            do_refresh(organizer)
            do_refresh(organizer)
    assert len(mailoutbox) == 2
    body = mailoutbox[1].body
    assert "Zamjenjuje: 14504100762_ZA_SVA_PRODAJNA_MJESTA_USLUGE_001_20261117_0300.csv" in body
    assert '\n"Idu Hobiti ""Idu Dalje"" - regular";9,00;NE;8,00\n' in body
    assert '+ "Idu Hobiti ""Idu Dalje"" - regular";9,00;NE;8,00' in body
    assert "- Ulaznica;8,00;NE;8,00" in body
    assert "&quot;" not in body


def test_no_email_without_address(frozen, organizer, event, item, django_capture_on_commit_callbacks, mailoutbox):
    with django_capture_on_commit_callbacks(execute=True):
        do_refresh(organizer)
    assert mailoutbox == []
