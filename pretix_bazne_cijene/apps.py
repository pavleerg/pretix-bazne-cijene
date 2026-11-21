from django.utils.translation import gettext_lazy

from . import __version__

try:
    from pretix.base.plugins import PLUGIN_LEVEL_ORGANIZER, PluginConfig
except ImportError:
    raise RuntimeError("Please use pretix 2026.3 or above to run this plugin!")


class PluginApp(PluginConfig):
    default = True
    name = "pretix_bazne_cijene"
    verbose_name = "Bazne cijene"

    class PretixPluginMeta:
        name = gettext_lazy("Bazne cijene (Croatian price display rules)")
        author = "Pavle Ergović (KSET)"
        description = gettext_lazy(
            "Shows the Croatian base price (bazna cijena) next to the price of every ticket, as "
            "required by the Consumer Protection Act (ZZP) Art. 7 and the Pravilnik o isticanju "
            "bazne cijene proizvoda (NN 132/2026), and publishes a machine-readable price list."
        )
        visible = True
        version = __version__
        category = "FEATURE"
        compatibility = "pretix>=2026.3.0"
        level = PLUGIN_LEVEL_ORGANIZER
        settings_links = []
        navigation_links = []

    def ready(self):
        from . import signals  # NOQA
