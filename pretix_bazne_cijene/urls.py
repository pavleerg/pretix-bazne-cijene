from django.urls import re_path

from . import views

urlpatterns = [
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/bazne-cijene/$",
        views.ReferencePriceListView.as_view(),
        name="index",
    ),
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/bazne-cijene/(?P<reference>\d+)/$",
        views.ReferencePriceEditView.as_view(),
        name="edit",
    ),
    re_path(
        r"^control/organizer/(?P<organizer>[^/]+)/bazne-cijene/$",
        views.OrganizerSettingsView.as_view(),
        name="settings",
    ),
    re_path(
        r"^control/organizer/(?P<organizer>[^/]+)/bazne-cijene/rebuild/$",
        views.RebuildNowView.as_view(),
        name="rebuild",
    ),
]

organizer_patterns = [
    re_path(r"^cijene/$", views.price_list_archive, name="archive"),
    re_path(r"^cijene/cjenik\.csv$", views.current_price_list, name="current"),
    re_path(
        r"^cijene/arhiva/(?P<version>\d+)\.csv$",
        views.archived_price_list,
        name="archived",
    ),
]
