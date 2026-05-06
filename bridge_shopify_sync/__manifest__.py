# -*- coding: utf-8 -*-
# ############################################################################
#
#     Shopify Bridge Sync
#
#     Copyright (C) 2026-TODAY Links For Everyone
#     Author: Salman Shahid
#
#     You can modify it under the terms of the GNU LESSER
#     GENERAL PUBLIC LICENSE (LGPL v3), Version 3.
#
#     This program is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU LESSER GENERAL PUBLIC LICENSE (LGPL v3) for more details.
#
#     You should have received a copy of the GNU LESSER GENERAL PUBLIC LICENSE
#     (LGPL v3) along with this program.
#     If not, see <http://www.gnu.org/licenses/ >.
#
# ############################################################################

{
    'name': 'Shopify Bridge Sync',
    'version': '18.0.1.0.0',
    'sequence': 1,
    'summary': 'Sync products, orders and customers with Shopify',
    'description': """
Shopify Bridge Sync
===================

Connect your Shopify stores to Odoo and sync:

**Products**
  - Import products with images, variants and inventory
  - Auto-sync on save to Shopify
  - Full product management with SEO fields

**Orders**
  - Sync orders in real-time
  - Cancel orders from Odoo to Shopify
  - Track fulfillments and shipping

**Customers**
  - Import customers with addresses
  - Auto-sync customer updates
  - Marketing preferences and tax status

**Features**
  - Dashboard with store statistics
  - Multi-store support
  - Sync journal for tracking
  - Raw JSON debug data

**Auto Sync**
  - Changes in Odoo automatically push to Shopify
  - Toggle per record to enable/disable

For more information, visit our website.
    """,
    'author': 'Salman Shahid',
    'category': 'Sales',
    'maintainer': 'Salman Shahid',
    'website': 'https://linksforeveryone.com ',
    'license': 'LGPL-3',
    'price': 100.0,
    'currency': 'USD',
    'depends': ['base', 'mail', 'sale_management', 'contacts'],
    'external_dependencies': {
        'python': ['requests'],
    },
    'data': [
        'security/ir.model.access.csv',
        'views/sync_store_views.xml',
        'views/sync_catalog_views.xml',
        'views/sync_sale_views.xml',
        'views/sync_contact_views.xml',
        'views/sync_journal_views.xml',
        'views/sync_dashboard_views.xml',
        'views/sync_wizard_views.xml',
        'views/sync_menus.xml',
    ],
    'images': [
        'static/description/banner.png',
        'static/description/icon.png',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'assets': {
        'web.assets_backend': [
            'bridge_shopify_sync/static/src/scss/dashboard.scss',
        ],
    },
}
