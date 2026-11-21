from decimal import Decimal
from django import forms
from django.utils.translation import gettext_lazy as _
from pretix.base.forms import SettingsForm
from pretix.base.timemachine import time_machine_now

from .conf import TIMEZONE
from .models import ReferencePrice
from .pricing import validate_label


class BazneCijeneSettingsForm(SettingsForm):
    bazne_cijene_label = forms.CharField(
        label=_("Base price label"),
        help_text=_(
            "Shown next to the ticket price, always in Croatian, whatever language the shop is "
            "shown in. Use {date} for the reference date and {price} for the base price. The text "
            "must say clearly that this is the base price, so it cannot be confused with the "
            "current price (ZZP Art. 7, Pravilnik NN 132/2026 Art. 5 and 6), "
            'e.g. "Cijena na {date}: {price}".'
        ),
        max_length=200,
        required=True,
    )
    bazne_cijene_pricelist_enabled = forms.BooleanField(
        label=_("Publish the price list of services"),
        help_text=_("A CSV file with every ticket on sale, its retail price and its base price."),
        required=False,
    )
    bazne_cijene_seller_name = forms.CharField(
        label=_("Seller"),
        help_text=_(
            'The legal entity selling the tickets, shown on the price list page, e.g. '
            '"Savez studenata Fakulteta elektrotehnike i računarstva (SS FER)".'
        ),
        max_length=200,
        required=False,
    )
    bazne_cijene_seller_address = forms.CharField(
        label=_("Seller address"),
        help_text=_('e.g. "Unska ulica 3, Zagreb".'),
        max_length=200,
        required=False,
    )
    bazne_cijene_oib = forms.CharField(
        label=_("Seller OIB"),
        help_text=_("Starts the price list file name. The price list is only published once it is set."),
        max_length=11,
        required=False,
    )
    bazne_cijene_pricelist_seq_offset = forms.IntegerField(
        label=_("Number of the last price list published outside pretix"),
        help_text=_(
            "Files are numbered from the next number on, e.g. enter 1 if your last list was "
            "..._USLUGE_001_.... Only used before the plugin publishes its first list."
        ),
        min_value=0,
        required=False,
    )

    bazne_cijene_notify_email = forms.EmailField(
        label=_("Notify by email"),
        help_text=_(
            "When a new price list is published, an email with the file name, a link and the "
            "changed rows is sent to this address. Leave empty to send no email."
        ),
        required=False,
    )

    def clean_bazne_cijene_oib(self):
        value = (self.cleaned_data.get("bazne_cijene_oib") or "").strip()
        if value and not (len(value) == 11 and value.isdigit()):
            raise forms.ValidationError(_("An OIB has exactly 11 digits."))
        return value

    def clean_bazne_cijene_pricelist_seq_offset(self):
        return self.cleaned_data.get("bazne_cijene_pricelist_seq_offset") or 0

    def clean_bazne_cijene_label(self):
        value = self.cleaned_data["bazne_cijene_label"].strip()
        try:
            validate_label(value)
        except ValueError:
            raise forms.ValidationError(_("Only the placeholders {date} and {price} can be used."))
        return value


class ReferencePriceCorrectionForm(forms.ModelForm):
    reason = forms.CharField(
        label=_("Reason for the correction"),
        help_text=_("Required. It is stored in the log together with your name."),
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    class Meta:
        model = ReferencePrice
        fields = ("price", "reference_date")
        widgets = {
            "reference_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def clean_price(self):
        price = self.cleaned_data["price"]
        if price < Decimal("0.00"):
            raise forms.ValidationError(_("The price cannot be negative."))
        return price

    def clean_reference_date(self):
        d = self.cleaned_data["reference_date"]
        if d > time_machine_now().astimezone(TIMEZONE).date():
            raise forms.ValidationError(_("The reference date cannot be in the future."))
        return d
