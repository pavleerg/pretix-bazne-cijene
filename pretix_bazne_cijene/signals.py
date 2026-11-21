import logging
from django.dispatch import receiver
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django_scopes import scope, scopes_disabled
from pretix.base.logentrytypes import EventLogEntryType, log_entry_types
from pretix.base.models import Organizer
from pretix.base.signals import event_live_issues, periodic_task
from pretix.base.timemachine import time_machine_now
from pretix.control.signals import nav_event, nav_organizer
from pretix.presale.signals import html_head, item_description

from . import conf  # NOQA: registers the settings defaults
from .conf import PLUGIN_NAME, pricelist_published
from .models import ReferencePrice

logger = logging.getLogger(__name__)


@receiver(item_description, dispatch_uid="bazne_cijene_item_description")
def item_description_receiver(sender, item, variation, subevent, **kwargs):
    from .pricing import label_html, only_with_voucher

    if variation is None and item.has_variations:
        # The label is shown on each variation instead.
        return None
    if only_with_voucher(item, variation):
        # Exempt (Pravilnik čl. 4).
        return None
    try:
        return label_html(sender, item, variation, subevent)
    except Exception:
        # Never break the shop because of the label.
        logger.exception("Could not render the base price label.")
        return None


@receiver(html_head, dispatch_uid="bazne_cijene_html_head")
def html_head_receiver(sender, request=None, **kwargs):
    # Label next to the price, distinct from it (Pravilnik čl. 5 st. 2-3, čl. 6), without overriding core templates.
    return format_html('<link rel="stylesheet" type="text/css" href="{}">', static("pretix_bazne_cijene/label.css"))


@receiver(periodic_task, dispatch_uid="bazne_cijene_periodic")
def periodic_refresh(sender, **kwargs):
    from .pricelist import rebuild_due
    from .tasks import daily_rebuild_task

    now = time_machine_now()
    with scopes_disabled():
        organizers = list(Organizer.objects.filter(plugins__contains=PLUGIN_NAME))
    for organizer in organizers:
        with scope(organizer=organizer):
            if not pricelist_published(organizer) or not rebuild_due(organizer, now):
                continue
        try:
            daily_rebuild_task.apply_async(kwargs={"organizer": organizer.pk, "user": None})
        except Exception:
            # Without a celery worker the task runs here; one organizer must not stop the others.
            logger.exception("Could not rebuild the price list of organizer %s", organizer.slug)


@receiver(nav_event, dispatch_uid="bazne_cijene_nav_event")
def nav_event_receiver(sender, request, **kwargs):
    return [
        {
            "label": _("Base prices"),
            "url": reverse(
                "plugins:pretix_bazne_cijene:index",
                kwargs={"organizer": request.organizer.slug, "event": request.event.slug},
            ),
            "parent": reverse(
                "control:event.items",
                kwargs={"organizer": request.organizer.slug, "event": request.event.slug},
            ),
            "active": request.resolver_match.namespace == "plugins:pretix_bazne_cijene",
        }
    ]


@receiver(nav_organizer, dispatch_uid="bazne_cijene_nav_organizer")
def nav_organizer_receiver(sender, request, **kwargs):
    if not request.user.has_organizer_permission(
        request.organizer, "organizer.settings.general:write", request=request
    ):
        return []
    return [
        {
            "label": _("Bazne cijene"),
            "url": reverse(
                "plugins:pretix_bazne_cijene:settings",
                kwargs={"organizer": request.organizer.slug},
            ),
            "icon": "eur",
            "active": request.resolver_match.namespace == "plugins:pretix_bazne_cijene",
        }
    ]


@receiver(event_live_issues, dispatch_uid="bazne_cijene_live_issues")
def live_issues_receiver(sender, **kwargs):
    issues = []
    if sender.currency != "EUR":
        issues.append(
            _(
                "Croatian price rules: the final price must be shown in euro (ZZP Art. 4), "
                "but this event uses the currency {currency}."
            ).format(currency=sender.currency)
        )
    if sender.settings.display_net_prices:
        issues.append(
            _(
                "Croatian price rules: the shop must show final prices including taxes "
                "(ZZP Art. 4), but it is set to show net prices."
            )
        )
    return " ".join(str(i) for i in issues) or None


@log_entry_types.new_from_dict(
    {
        "pretix_bazne_cijene.reference.captured": _("Base price recorded when the ticket was first offered."),
        "pretix_bazne_cijene.reference.changed": _("Base price corrected manually."),
    }
)
class ReferencePriceLogEntryType(EventLogEntryType):
    object_type = ReferencePrice
    object_link_wrapper = _("Base price of {val}")
    object_link_viewname = "plugins:pretix_bazne_cijene:edit"
    object_link_argname = "reference"
