from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_scopes import ScopedManager
from pretix.base.models import LoggedModel


class ReferencePrice(LoggedModel):
    """
    Base price (ZZP čl. 7, Pravilnik NN 132/2026 čl. 3): the gross price before any discount
    on ``reference_date``. Set once; only a manual correction changes it.
    """

    SOURCE_CAPTURE = "capture"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = (
        (SOURCE_CAPTURE, _("Recorded when first offered")),
        (SOURCE_MANUAL, _("Corrected manually")),
    )

    event = models.ForeignKey(
        "pretixbase.Event",
        on_delete=models.CASCADE,
        related_name="bazne_cijene_reference_prices",
    )
    item = models.ForeignKey(
        "pretixbase.Item",
        on_delete=models.CASCADE,
        related_name="+",
    )
    variation = models.ForeignKey(
        "pretixbase.ItemVariation",
        on_delete=models.CASCADE,
        related_name="+",
        null=True,
        blank=True,
    )
    subevent = models.ForeignKey(
        "pretixbase.SubEvent",
        on_delete=models.CASCADE,
        related_name="+",
        null=True,
        blank=True,
    )
    price = models.DecimalField(
        verbose_name=_("Base price (incl. taxes)"),
        max_digits=13,
        decimal_places=2,
    )
    currency = models.CharField(max_length=10)
    reference_date = models.DateField(verbose_name=_("Reference date"))
    captured_at = models.DateTimeField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    note = models.TextField(blank=True)
    updated = models.DateTimeField(auto_now=True)

    objects = ScopedManager(organizer="event__organizer")

    class Meta:
        ordering = ("event", "subevent", "item", "variation")
        constraints = [
            models.UniqueConstraint(
                fields=["item"],
                condition=Q(variation__isnull=True, subevent__isnull=True),
                name="bazne_cijene_unique_item",
            ),
            models.UniqueConstraint(
                fields=["item", "variation"],
                condition=Q(variation__isnull=False, subevent__isnull=True),
                name="bazne_cijene_unique_variation",
            ),
            models.UniqueConstraint(
                fields=["item", "subevent"],
                condition=Q(variation__isnull=True, subevent__isnull=False),
                name="bazne_cijene_unique_item_subevent",
            ),
            models.UniqueConstraint(
                fields=["item", "variation", "subevent"],
                condition=Q(variation__isnull=False, subevent__isnull=False),
                name="bazne_cijene_unique_variation_subevent",
            ),
        ]

    def __str__(self):
        parts = [str(self.item.name)]
        if self.variation_id:
            parts.append(str(self.variation.value))
        if self.subevent_id:
            parts.append(str(self.subevent))
        return " – ".join(parts)


class PriceListVersion(models.Model):
    """One published version of an organizer's price list of services ("cjenik usluga")."""

    organizer = models.ForeignKey(
        "pretixbase.Organizer",
        on_delete=models.CASCADE,
        related_name="bazne_cijene_price_lists",
    )
    created = models.DateTimeField(db_index=True)
    seq = models.PositiveIntegerField()
    content_hash = models.CharField(max_length=64)
    row_count = models.PositiveIntegerField(default=0)
    csv = models.TextField()

    objects = ScopedManager(organizer="organizer")

    class Meta:
        ordering = ("-created", "-pk")
        constraints = [
            models.UniqueConstraint(fields=["organizer", "seq"], name="bazne_cijene_unique_seq"),
        ]
