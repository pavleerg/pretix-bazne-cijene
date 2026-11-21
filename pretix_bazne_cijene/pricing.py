"""
Base price (ZZP čl. 7 st. 1, NN 59/2026; Pravilnik NN 132/2026 br. 1583 čl. 3): the retail
price on 10 September 2026, or on the first day offered if later; if discounted that day,
the price before the discount (the crossed-out original price).
"""
from typing import Optional

import logging
import string
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from django.db import IntegrityError, transaction
from django.utils import translation
from django.utils.html import escape
from pretix.base.models import Quota
from pretix.base.templatetags.money import money_filter
from pretix.base.timemachine import time_machine_now

from .conf import BASE_PRICE_DATE, DEFAULT_LABEL, LANGUAGE, TIMEZONE
from .models import ReferencePrice

logger = logging.getLogger(__name__)

_state = threading.local()

_CACHE_ATTR = "_bazne_cijene_refs"

LABEL_FIELDS = {"date", "price"}


def today(now=None) -> date:
    return (now or time_machine_now()).astimezone(TIMEZONE).date()


def reference_date_for(first_offered: date) -> date:
    """Pravilnik čl. 3."""
    return max(first_offered, BASE_PRICE_DATE)


@contextmanager
def capture_disabled():
    previous = getattr(_state, "disabled", False)
    _state.disabled = True
    try:
        yield
    finally:
        _state.disabled = previous


def only_with_voucher(item, variation=None) -> bool:
    """Pravilnik čl. 4: voucher-only tickets have personalised prices, so no base price."""
    if item.hide_without_voucher or item.require_voucher:
        return True
    return variation is not None and variation.hide_without_voucher


@dataclass
class CurrentPrice:
    retail: Decimal
    regular: Decimal
    special_sale: bool


def list_price(item, variation=None, subevent=None) -> Decimal:
    """Mirrors pretix.presale.productlist: a variation ignores the item's per-date override."""
    if variation is not None:
        if subevent is not None:
            override = subevent.var_price_overrides.get(variation.pk)
            if override is not None:
                return override
        return variation.price
    if subevent is not None:
        override = subevent.item_price_overrides.get(item.pk)
        if override is not None:
            return override
    return item.default_price


def _gross(event, item, variation, price, base_price_is="auto") -> Decimal:
    obj = variation if variation is not None else item
    return obj.tax(
        price,
        base_price_is=base_price_is,
        currency=event.currency,
        include_bundled=True,
    ).gross


def current_price(event, item, variation=None, subevent=None) -> CurrentPrice:
    """
    ``item`` and ``variation`` must be fresh instances: the shop product list replaces
    ``original_price`` with a taxed price after sending ``item_description``.
    """
    price = list_price(item, variation, subevent)
    retail = _gross(event, item, variation, price)

    original = variation.original_price if variation is not None else None
    if original is None:
        original = item.original_price

    if original is not None and original > price:
        regular = _gross(event, item, variation, original, base_price_is="gross")
        return CurrentPrice(retail=retail, regular=regular, special_sale=True)
    return CurrentPrice(retail=retail, regular=retail, special_sale=False)


def lookup(event, item, variation=None, subevent=None) -> Optional[ReferencePrice]:
    return ReferencePrice.objects.filter(
        event=event, item=item, variation=variation, subevent=subevent
    ).first()


def _refs_for(event, subevent) -> dict:
    """
    ``{(item_id, variation_id): ref}`` for one date, cached on the event object, which lives
    for one request (or one price list rebuild).
    """
    cache = event.__dict__.setdefault(_CACHE_ATTR, {})
    key = subevent.pk if subevent is not None else None
    if key not in cache:
        cache[key] = {
            (r.item_id, r.variation_id): r
            for r in ReferencePrice.objects.filter(event=event, subevent=subevent).order_by()
        }
    return cache[key]


def _is_offered(event, item, variation, subevent) -> bool:
    obj = variation if variation is not None else item
    if not obj.quotas.filter(subevent=subevent).exists():
        return False
    if event.settings.hide_sold_out:
        availability, _ = obj.check_quotas(subevent=subevent, include_bundled=True)
        if availability < Quota.AVAILABILITY_RESERVED:
            return False
    return True


def capture_allowed(event, item, variation=None, subevent=None) -> bool:
    if getattr(_state, "disabled", False):
        return False
    if not event.live or event.testmode:
        return False
    if subevent is not None and not subevent.active:
        return False
    if today() < BASE_PRICE_DATE:
        # Pravilnik čl. 3: the 10 September 2026 price is not known yet.
        return False
    if only_with_voucher(item, variation):
        # Exempt (Pravilnik čl. 4).
        return False
    return _is_offered(event, item, variation, subevent)


def capture(event, item, variation=None, subevent=None, now=None) -> ReferencePrice:
    """Pravilnik čl. 3: store the price before any discount on the day first offered."""
    now = now or time_machine_now()
    prices = current_price(event, item, variation, subevent)
    try:
        with transaction.atomic():
            ref = ReferencePrice.objects.create(
                event=event,
                item=item,
                variation=variation,
                subevent=subevent,
                price=prices.regular,
                currency=event.currency,
                reference_date=reference_date_for(today(now)),
                captured_at=now,
                source=ReferencePrice.SOURCE_CAPTURE,
            )
    except IntegrityError:
        # Another request stored it first.
        ref = lookup(event, item, variation, subevent)
        if ref is None:
            raise
        return ref
    ref.log_action(
        "pretix_bazne_cijene.reference.captured",
        data={
            "price": str(ref.price),
            "reference_date": ref.reference_date.isoformat(),
            "item": item.pk,
            "variation": variation.pk if variation else None,
            "subevent": subevent.pk if subevent else None,
        },
    )
    return ref


def get_or_capture(event, item, variation=None, subevent=None) -> Optional[ReferencePrice]:
    refs = _refs_for(event, subevent)
    key = (item.pk, variation.pk if variation is not None else None)
    ref = refs.get(key)
    if ref is None and capture_allowed(event, item, variation, subevent):
        ref = refs[key] = capture(event, item, variation, subevent)
    return ref


def format_date(d: date) -> str:
    # e.g. "10.9.2026.": numeric formats such as 09/10/2026 are ambiguous (Pravilnik čl. 5 st. 3).
    return f"{d.day}.{d.month}.{d.year}."


def validate_label(template: str) -> None:
    for _, field, spec, conversion in string.Formatter().parse(template):
        if field is None:
            continue
        if field not in LABEL_FIELDS or spec or conversion:
            raise ValueError(f"Invalid placeholder {{{field}}}")


def label_text(event, price: Decimal, d: date, currency: str) -> str:
    with translation.override(LANGUAGE):
        price_text = money_filter(price, currency)
    values = {"date": format_date(d), "price": price_text}
    template = str(event.settings.bazne_cijene_label or "")
    try:
        validate_label(template)
        text = template.format(**values)
    except ValueError:
        logger.warning("Invalid bazne_cijene_label template %r, using the default.", template)
        text = ""
    if not text.strip():
        text = DEFAULT_LABEL.format(**values)
    return text


def label_html(event, item, variation=None, subevent=None) -> str:
    """
    Moved next to the price by label.css (Pravilnik čl. 5 st. 2-3, čl. 6). While nothing
    can be stored (not live, test mode), it previews the current price with today's date.
    """
    ref = get_or_capture(event, item, variation, subevent)
    if ref is not None:
        price, d, currency = ref.price, ref.reference_date, ref.currency
    else:
        price = current_price(event, item, variation, subevent).regular
        d, currency = reference_date_for(today()), event.currency
    return '<span class="bazne-cijene-label">{}</span>'.format(
        escape(label_text(event, price, d, currency))
    )
