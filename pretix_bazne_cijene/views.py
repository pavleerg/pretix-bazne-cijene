import re
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import http_date
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import FormView, ListView, UpdateView
from pretix.base.timemachine import time_machine_now
from pretix.base.views.tasks import AsyncAction
from pretix.control.permissions import (
    EventPermissionRequiredMixin,
    OrganizerPermissionRequiredMixin,
)
from pretix.control.views.organizer import OrganizerDetailViewMixin
from pretix.multidomain.urlreverse import eventreverse

from .conf import RETENTION_DAYS, TIMEZONE, plugin_enabled, pricelist_published
from .forms import BazneCijeneSettingsForm, ReferencePriceCorrectionForm
from .models import PriceListVersion, ReferencePrice
from .pricelist import current_version, filename, public_url, replaced_at
from .tasks import rebuild_price_list


class ReferencePriceListView(EventPermissionRequiredMixin, ListView):
    permission = None
    template_name = "pretix_bazne_cijene/index.html"
    context_object_name = "references"

    def get_queryset(self):
        return (
            ReferencePrice.objects.filter(event=self.request.event)
            .select_related("item", "variation", "subevent")
            .order_by("subevent__date_from", "item__position", "item_id", "variation__position")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_edit"] = self.request.user.has_event_permission(
            self.request.organizer, self.request.event, "event.items:write", request=self.request
        )
        return ctx


class ReferencePriceEditView(EventPermissionRequiredMixin, UpdateView):
    permission = "event.items:write"
    template_name = "pretix_bazne_cijene/edit.html"
    form_class = ReferencePriceCorrectionForm
    context_object_name = "reference"

    def get_object(self, queryset=None):
        return get_object_or_404(
            ReferencePrice.objects.select_related("item", "variation", "subevent"),
            event=self.request.event,
            pk=self.kwargs["reference"],
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["logs"] = self.object.all_logentries()[:20]
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        old = ReferencePrice.objects.get(pk=self.object.pk)
        ref = form.save(commit=False)
        ref.source = ReferencePrice.SOURCE_MANUAL
        stamp = time_machine_now().astimezone(TIMEZONE).strftime("%Y-%m-%d %H:%M")
        line = f"[{stamp}, {self.request.user.email}] {form.cleaned_data['reason']}"
        ref.note = f"{ref.note}\n{line}".strip()
        ref.save()
        ref.log_action(
            "pretix_bazne_cijene.reference.changed",
            user=self.request.user,
            data={
                "old_price": str(old.price),
                "old_reference_date": old.reference_date.isoformat(),
                "price": str(ref.price),
                "reference_date": ref.reference_date.isoformat(),
                "reason": form.cleaned_data["reason"],
            },
        )
        messages.success(self.request, _("The base price has been corrected."))
        return redirect(
            "plugins:pretix_bazne_cijene:index",
            organizer=self.request.organizer.slug,
            event=self.request.event.slug,
        )

    def form_invalid(self, form):
        messages.error(self.request, _("We could not save your changes. See below for details."))
        return super().form_invalid(form)


def _settings_url(organizer):
    return reverse("plugins:pretix_bazne_cijene:settings", kwargs={"organizer": organizer.slug})


class OrganizerSettingsView(OrganizerDetailViewMixin, OrganizerPermissionRequiredMixin, FormView):
    permission = "organizer.settings.general:write"
    template_name = "pretix_bazne_cijene/settings.html"
    form_class = BazneCijeneSettingsForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["obj"] = self.request.organizer
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["plugin_enabled"] = plugin_enabled(self.request.organizer)
        ctx["public_url"] = public_url(self.request.organizer, "current")
        ctx["archive_url"] = public_url(self.request.organizer, "archive")
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        organizer = self.request.organizer
        form.save()
        if form.has_changed():
            organizer.log_action(
                "pretix.organizer.settings",
                user=self.request.user,
                data={k: form.cleaned_data.get(k) for k in form.changed_data},
            )
        if pricelist_published(organizer) and not PriceListVersion.objects.filter(organizer=organizer).exists():
            kwargs = {"organizer": organizer.pk, "user": self.request.user.pk}
            transaction.on_commit(lambda: rebuild_price_list.apply_async(kwargs=kwargs))
        messages.success(self.request, _("Your changes have been saved."))
        return redirect(_settings_url(organizer))

    def form_invalid(self, form):
        messages.error(self.request, _("We could not save your changes. See below for details."))
        return super().form_invalid(form)


class RebuildNowView(OrganizerPermissionRequiredMixin, AsyncAction, View):
    permission = "organizer.settings.general:write"
    task = rebuild_price_list

    def get_success_url(self, value):
        return _settings_url(self.request.organizer)

    def get_error_url(self):
        return _settings_url(self.request.organizer)

    def get_success_message(self, value):
        if value["changed"]:
            return _("New price list published: {name}").format(name=value["name"])
        return _("Nothing changed, the current price list stays: {name}").format(name=value["name"])

    def post(self, request, *args, **kwargs):
        organizer = request.organizer
        if not pricelist_published(organizer):
            if not organizer.settings.bazne_cijene_oib:
                messages.error(request, _("Enter the seller OIB first, it is part of the file name."))
            else:
                messages.error(request, _("The price list is not published, so it cannot be rebuilt."))
            return redirect(_settings_url(organizer))
        return self.do(organizer=organizer.pk, user=request.user.pk)


def _require_public(request):
    organizer = request.organizer
    if not pricelist_published(organizer):
        raise Http404(_("The price list is not published."))
    return organizer


def _require_current(organizer):
    current = current_version(organizer)
    if current is None:
        raise Http404(_("The price list is not published yet."))
    return current


def _file_response(organizer, version):
    # UTF-8 with BOM, so spreadsheet programs read the Croatian characters correctly.
    response = HttpResponse(("﻿" + version.csv).encode("utf-8"), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'inline; filename="{}"'.format(filename(organizer, version))
    # Automated price comparison tools must be able to fetch the list.
    response["Access-Control-Allow-Origin"] = "*"
    response["Cache-Control"] = "no-cache"
    response["Last-Modified"] = http_date(version.created.timestamp())
    return response


def current_price_list(request, **kwargs):
    organizer = _require_public(request)
    return _file_response(organizer, _require_current(organizer))


def archived_price_list(request, version, **kwargs):
    organizer = _require_public(request)
    obj = get_object_or_404(PriceListVersion, organizer=organizer, pk=version)
    return _file_response(organizer, obj)


def _short_name(seller_name):
    # "Savez studenata ... (SS FER)" -> "SS FER"
    match = re.search(r"\(([^()]+)\)\s*$", seller_name)
    return match.group(1) if match else seller_name


def price_list_archive(request, **kwargs):
    organizer = _require_public(request)
    s = organizer.settings
    current = _require_current(organizer)
    replaced = replaced_at(organizer)
    archive = [
        {
            "name": filename(organizer, v),
            "url": eventreverse(organizer, "plugins:pretix_bazne_cijene:archived", kwargs={"version": v.pk}),
            "replaced": replaced[v.pk].astimezone(TIMEZONE).strftime("%d.%m.%Y. u %H:%M"),
        }
        for v in PriceListVersion.objects.filter(organizer=organizer).only("pk", "created", "seq")
        if v.pk in replaced
    ]
    seller = ", ".join(p for p in (s.bazne_cijene_seller_name, s.bazne_cijene_seller_address) if p)
    return render(
        request,
        "pretix_bazne_cijene/archive.html",
        {
            "title_name": _short_name(s.bazne_cijene_seller_name) or str(organizer.name),
            "seller": seller,
            "oib": s.bazne_cijene_oib,
            "current_name": filename(organizer, current),
            "current_url": eventreverse(organizer, "plugins:pretix_bazne_cijene:current"),
            "archive": archive,
            "retention_days": RETENTION_DAYS,
        },
    )
