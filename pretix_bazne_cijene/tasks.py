import logging
from pretix.base.services.tasks import ProfiledOrganizerUserTask
from pretix.base.timemachine import time_machine_now
from pretix.celery_app import app

from .models import PriceListVersion
from .pricelist import daily_rebuild, filename, refresh

logger = logging.getLogger(__name__)


@app.task(base=ProfiledOrganizerUserTask)
def rebuild_price_list(organizer, user=None) -> dict:
    before = PriceListVersion.objects.filter(organizer=organizer).values_list("pk", flat=True).first()
    version = refresh(organizer)
    return {"name": filename(organizer, version), "changed": version.pk != before}


@app.task(base=ProfiledOrganizerUserTask)
def daily_rebuild_task(organizer, user=None) -> None:
    try:
        daily_rebuild(organizer)
    except Exception:
        # Stored outside the rolled-back transaction, so the next attempt waits
        # RETRY_AFTER_ERROR instead of failing on every cron run.
        organizer.settings.bazne_cijene_pricelist_failed = time_machine_now().isoformat()
        logger.exception("Could not rebuild the price list of organizer %s", organizer.slug)
        raise
