"""All settings live on the organizer; ``event.settings`` reads fall back to it."""
from datetime import date, timedelta
from pretix.base.settings import settings_hierarkey
from zoneinfo import ZoneInfo

PLUGIN_NAME = "pretix_bazne_cijene"

# Pravilnik čl. 3: the base price is the retail price applied on this day.
BASE_PRICE_DATE = date(2026, 9, 10)

TIMEZONE = ZoneInfo("Europe/Zagreb")

LANGUAGE = "hr"

# Pravilnik (NN 132/2026) čl. 5-6: the label must not be confusable with the retail price.
DEFAULT_LABEL = "Cijena na {date}: {price}"

REBUILD_HOUR = 3

RETRY_AFTER_ERROR = timedelta(minutes=60)

RETENTION_DAYS = 15

settings_hierarkey.add_default("bazne_cijene_label", DEFAULT_LABEL, str)
settings_hierarkey.add_default("bazne_cijene_pricelist_enabled", True, bool)

settings_hierarkey.add_default("bazne_cijene_seller_name", "", str)
settings_hierarkey.add_default("bazne_cijene_seller_address", "", str)
settings_hierarkey.add_default("bazne_cijene_oib", "", str)
settings_hierarkey.add_default("bazne_cijene_pricelist_seq_offset", 0, int)
settings_hierarkey.add_default("bazne_cijene_notify_email", "", str)
settings_hierarkey.add_default("bazne_cijene_pricelist_checked", "", str)
settings_hierarkey.add_default("bazne_cijene_pricelist_failed", "", str)


def plugin_enabled(organizer) -> bool:
    return PLUGIN_NAME in organizer.get_plugins()


def pricelist_published(organizer) -> bool:
    return bool(
        plugin_enabled(organizer)
        and organizer.settings.bazne_cijene_pricelist_enabled
        # The OIB is part of the file name, so nothing is published without it.
        and organizer.settings.bazne_cijene_oib
    )
