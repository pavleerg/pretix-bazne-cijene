Bazne cijene
==========================

This is a plugin for `pretix`_.

It shows the Croatian base price ("bazna cijena") next to the price of every ticket in the
shop, as required by the Consumer Protection Act (ZZP, NN 59/2026) Art. 7 Para. 1 and the
`Pravilnik o isticanju bazne cijene proizvoda`_ (NN 132/2026, br. 1583). It also publishes a
machine-readable price list.

How the base price is determined
--------------------------------

For every ticket (product, variation and event date) offered in the web shop:

* offered on or before 10 September 2026: the retail price applied on that day, shown with
  the date 10.9.2026. (Pravilnik Art. 3);
* first offered later: the retail price on the day it was first offered, shown with that
  date (Art. 3);
* if a discount (crossed-out "original price") applied on that day, the price before the
  discount (Art. 3);
* always the gross price, including taxes.

The base price is recorded the first time a ticket is shown in the live shop and is never
changed automatically afterwards. It can be corrected in the control panel, and every
correction requires a reason and is logged. Tickets that can only be seen or bought with a
voucher show no base price, because their prices are personalised (Art. 4).

The label defaults to ``Cijena na {date}: {price}``, e.g. ``Cijena na 10.9.2026.: 14,00 €``,
and is always shown in Croatian, whatever language the shop is shown in. It can be changed
per organizer. It is added to the ticket description and
moved next to the price by a small stylesheet, so no pretix templates are overridden.

Price list of services
----------------------

With "Publish the price list of services" enabled and the seller OIB set (Organizer →
Bazne cijene), the plugin publishes:

* ``/<organizer>/cijene/``: the price list page (seller, current file, archive of the
  last 15 days);
* ``/<organizer>/cijene/cjenik.csv``: the current file.

The CSV has the columns ``Naziv usluge;Maloprodajna cijena;Posebni oblik prodaje (DA/NE);Bazna
cijena`` (UTF-8 with BOM, ``;``, decimal comma), one row per ticket on sale at any time that
day, from the rebuild until midnight (a ticket that goes on sale at 11:00 is already in the
03:00 list), named by the ticket's internal name. Files are named
``<OIB>_ZA_SVA_PRODAJNA_MJESTA_USLUGE_<seq>_<na dan>_0300.csv``, where the date ("na dan") is
the day the list was generated.

The list is rebuilt once a day, at the first run of pretix' periodic tasks after 03:00 (Zagreb
time), so the server must run ``pretix cron`` regularly (e.g. every 10 minutes). A new version
is stored only if a price or the set of tickets on sale changed. Saving a ticket or opening the
list does not rebuild it; changes made during the day are published at the next 03:00 rebuild.
The first list is published when the settings are saved with the OIB set (or with "Rebuild
the price list now"); until then the public addresses return 404. Rebuilds run as background
tasks (celery), never inside a shop request.
If "Notify by email" is set, that address gets an email with the new file name, a link and
the changed rows whenever a new version is published.

Development setup
-----------------

1. Make sure that you have a working `pretix development setup`_.

2. Clone this repository.

3. Activate the virtual environment you use for pretix development.

4. Execute ``pip install -e .`` within this directory to register this application with
   pretix's plugin registry.

5. Restart your local pretix server. You can now enable the plugin for your organizer in
   the organizer's 'plugins' tab.

To run the tests::

    python -m pytest tests

This plugin has CI set up to enforce a few code style rules. To check locally, you need these
packages installed::

    pip install flake8 isort black

To check your plugin for rule violations, run::

    black --check .
    isort -c .
    flake8 .

You can auto-fix some of these issues by running::

    isort .
    black .


License
-------

To be decided by the maintainers.


.. _pretix: https://github.com/pretix/pretix
.. _pretix development setup: https://docs.pretix.eu/en/latest/development/setup.html
.. _Pravilnik o isticanju bazne cijene proizvoda: https://narodne-novine.nn.hr/clanci/sluzbeni/2026_11_132_1583.html
