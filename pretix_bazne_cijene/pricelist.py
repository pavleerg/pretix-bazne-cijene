"""
Price list of services ("cjenik usluga") in the format SS FER published by hand, e.g.
``<OIB>_ZA_SVA_PRODAJNA_MJESTA_USLUGE_<seq>_<na dan>_0300.csv``: one row per ticket on sale
in the web shop at any time from the rebuild until midnight.
"""
from typing import Optional

import csv
import hashlib
import io
import json
from datetime import datetime, time, timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import Max
from django.utils import translation
from pretix.base.models import ItemVariation, Organizer
from pretix.base.models.items import SubEventItem, SubEventItemVariation
from pretix.base.services.mail import mail
from pretix.base.timemachine import time_machine_now, time_machine_now_assigned

try:
    from pretix.presale.productlist import prepare_item_list_for_shop
except ImportError:  # pretix < 2026.7 (production: 2026.5.2)
    from pretix.presale.views.event import (
        get_grouped_items as prepare_item_list_for_shop,
    )
try:
    from pretix.multidomain.urlreverse import eventreverse_absolute
except ImportError:  # pretix < 2026.6 (production: 2026.5.2)
    from pretix.multidomain.urlreverse import (
        build_absolute_uri as eventreverse_absolute,
    )

from .conf import LANGUAGE, REBUILD_HOUR, RETENTION_DAYS, RETRY_AFTER_ERROR, TIMEZONE
from .models import PriceListVersion
from .pricing import capture_disabled, current_price, get_or_capture

COLUMNS = (
    "Naziv usluge",
    "Maloprodajna cijena",
    "Posebni oblik prodaje (DA/NE)",
    "Bazna cijena",
)

FILENAME_SCOPE = "ZA_SVA_PRODAJNA_MJESTA_USLUGE"
# Always the daily rebuild time, whenever the version was built.
FILENAME_TIME = "0300"


def public_url(organizer, name, **kwargs):
    return eventreverse_absolute(organizer, f"plugins:pretix_bazne_cijene:{name}", kwargs=kwargs)


def _money(value: Decimal) -> str:
    return str(Decimal(value).quantize(Decimal("0.01"))).replace(".", ",")


def _service_name(item) -> str:
    return (item.internal_name or "").strip() or str(item.name)


def build_row(event, item, variation=None, subevent=None) -> dict:
    prices = current_price(event, item, variation, subevent)
    ref = get_or_capture(event, item, variation, subevent)
    base = ref.price if ref is not None else prices.regular
    return {
        "Naziv usluge": _service_name(item),
        "Maloprodajna cijena": _money(prices.retail),
        "Posebni oblik prodaje (DA/NE)": "DA" if prices.special_sale else "NE",
        "Bazna cijena": _money(base),
    }


def _dates_on_sale(event):
    if event.has_subevents:
        candidates = list(event.subevents.filter(active=True).order_by("date_from", "pk"))
    else:
        candidates = [None]
    for subevent in candidates:
        target = subevent or event
        if target.presale_has_ended:
            continue
        if not target.presale_is_running and not event.settings.show_items_outside_presale_period:
            continue
        yield subevent


def end_of_day(now) -> datetime:
    return datetime.combine(now.astimezone(TIMEZONE).date() + timedelta(days=1), time.min, tzinfo=TIMEZONE)


def _sale_changes(event, start, end):
    """Moments in ``[start, end)`` at which a ticket can go on or off sale, ``start`` included."""
    values = [event.presale_start, event.presale_end, event.date_to]
    for row in event.subevents.filter(active=True).values_list("presale_start", "presale_end", "date_to"):
        values.extend(row)
    for qs in (
        event.items.all(),
        ItemVariation.objects.filter(item__event=event),
        SubEventItem.objects.filter(subevent__event=event),
        SubEventItemVariation.objects.filter(subevent__event=event),
    ):
        for row in qs.values_list("available_from", "available_until"):
            values.extend(row)
    return sorted({start} | {v for v in values if v is not None and start < v < end})


def _offered_keys(event, channel, now) -> dict:
    """``{(subevent pk, item pk, variation pk): subevent}`` in the order of the shop."""
    offered = {}
    for moment in _sale_changes(event, now, end_of_day(now)):
        # Rendering descriptions would capture base prices at the evaluated moment;
        # build_row captures them at the real time.
        with time_machine_now_assigned(moment), capture_disabled():
            for subevent in _dates_on_sale(event):
                items, _ = prepare_item_list_for_shop(
                    event,
                    channel=channel,
                    subevent=subevent,
                    allow_addons=True,
                    allow_cross_sell=True,
                )
                for item in items:
                    variations = item.available_variations if item.has_variations else [None]
                    for variation in variations:
                        key = (subevent.pk if subevent else None, item.pk, variation.pk if variation else None)
                        offered.setdefault(key, subevent)
    return offered


def iter_offered(organizer, now=None):
    """
    Yield ``(event, item, variation, subevent)`` offered until midnight. The shop list only
    changes when a sales period starts or ends, so it is evaluated only at those moments.
    """
    now = now or time_machine_now()
    channel = organizer.sales_channels.filter(identifier="web").first()
    if channel is None:
        return
    events = organizer.events.filter(live=True, testmode=False).order_by("date_from", "slug")
    for event in events:
        offered = _offered_keys(event, channel, now)
        if not offered:
            continue
        # Fresh instances: the shop's product list replaces original_price on its objects.
        items = event.items.select_related("tax_rule").in_bulk({k[1] for k in offered})
        variations = (
            ItemVariation.objects.filter(item__event=event)
            .select_related("item", "item__tax_rule")
            .in_bulk({k[2] for k in offered if k[2] is not None})
        )
        entries = [
            (event, items[item_id], variations[var_id] if var_id is not None else None, subevent)
            for (_, item_id, var_id), subevent in offered.items()
        ]
        if event.has_subevents:
            # Stable sort: within a date, tickets going on sale later in the day stay last.
            entries.sort(key=lambda o: (o[3].date_from, o[3].pk))
        yield from entries


def offered_rows(organizer, now=None) -> list:
    with translation.override(LANGUAGE):
        return [build_row(*offer) for offer in iter_offered(organizer, now)]


def render_csv(rows) -> str:
    """The CSV text, without the BOM (added when the file is served)."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([row[c] for c in COLUMNS])
    return out.getvalue()


def filename(organizer, version) -> str:
    na_dan = version.created.astimezone(TIMEZONE).date()
    return "{oib}_{scope}_{seq:03d}_{day:%Y%m%d}_{time}.csv".format(
        oib=organizer.settings.bazne_cijene_oib,
        scope=FILENAME_SCOPE,
        seq=version.seq,
        day=na_dan,
        time=FILENAME_TIME,
    )


def _content_hash(rows) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _next_seq(organizer) -> int:
    last = PriceListVersion.objects.filter(organizer=organizer).aggregate(m=Max("seq"))["m"]
    if last is None:
        last = organizer.settings.bazne_cijene_pricelist_seq_offset or 0
    return last + 1


def _lock(organizer) -> None:
    """Serialise rebuilds of one organizer (cron, "Rebuild now", settings save) until commit."""
    Organizer.objects.select_for_update().filter(pk=organizer.pk).first()


def replaced_at(organizer) -> dict:
    """``{version pk: datetime it was replaced}`` for every version except the current one."""
    versions = list(
        PriceListVersion.objects.filter(organizer=organizer).order_by("created", "pk").only("pk", "created")
    )
    return {old.pk: new.created for old, new in zip(versions, versions[1:])}


def refresh(organizer, now=None) -> PriceListVersion:
    now = now or time_machine_now()
    with transaction.atomic():
        _lock(organizer)
        rows = offered_rows(organizer, now)
        content_hash = _content_hash(rows)
        latest = PriceListVersion.objects.filter(organizer=organizer).first()
        if latest is None or latest.content_hash != content_hash:
            previous = latest
            latest = PriceListVersion.objects.create(
                organizer=organizer,
                created=now,
                seq=_next_seq(organizer),
                content_hash=content_hash,
                row_count=len(rows),
                csv=render_csv(rows),
            )
            if organizer.settings.bazne_cijene_notify_email:
                version = latest
                transaction.on_commit(lambda: notify(organizer, version, previous))
        expired = [
            pk for pk, replaced in replaced_at(organizer).items()
            if replaced < now - timedelta(days=RETENTION_DAYS)
        ]
        PriceListVersion.objects.filter(organizer=organizer, pk__in=expired).exclude(pk=latest.pk).delete()
    return latest


def _changed_lines(new, old):
    old_lines = set(old.csv.splitlines()[1:]) if old is not None else set()
    return [line for line in new.csv.splitlines()[1:] if line not in old_lines]


def notify(organizer, version, previous) -> None:
    name = filename(organizer, version)
    mail(
        organizer.settings.bazne_cijene_notify_email,
        f"Cjenik usluga osvježen: {name}",
        "pretix_bazne_cijene/email/refreshed.txt",
        {
            "name": name,
            "previous_name": filename(organizer, previous) if previous is not None else "",
            "csv_url": public_url(organizer, "current"),
            "page_url": public_url(organizer, "archive"),
            "row_count": version.row_count,
            "lines": version.csv.splitlines(),
            "added": _changed_lines(version, previous) if previous is not None else [],
            "removed": _changed_lines(previous, version) if previous is not None else [],
        },
        organizer=organizer,
        locale=LANGUAGE,
        plain_text_only=True,
    )


def _rebuild_time(now):
    local = now.astimezone(TIMEZONE)
    return local.replace(hour=REBUILD_HOUR, minute=0, second=0, microsecond=0)


def rebuild_due(organizer, now) -> bool:
    rebuild_time = _rebuild_time(now)
    if now < rebuild_time:
        return False
    failed = organizer.settings.bazne_cijene_pricelist_failed
    if failed and now - datetime.fromisoformat(failed) < RETRY_AFTER_ERROR:
        return False
    last = organizer.settings.bazne_cijene_pricelist_checked
    return not last or datetime.fromisoformat(last) < rebuild_time


def daily_rebuild(organizer, now=None) -> Optional[PriceListVersion]:
    now = now or time_machine_now()
    with transaction.atomic():
        _lock(organizer)
        # Another worker may have run it while this one waited for the lock.
        organizer.settings.flush()
        if not rebuild_due(organizer, now):
            return None
        version = refresh(organizer, now=now)
        organizer.settings.bazne_cijene_pricelist_checked = now.isoformat()
    return version


def current_version(organizer) -> Optional[PriceListVersion]:
    """None before the first rebuild: the list is never built on demand."""
    return PriceListVersion.objects.filter(organizer=organizer).first()
