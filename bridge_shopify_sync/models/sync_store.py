# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
import requests
import logging

_logger = logging.getLogger(__name__)


class SyncStore(models.Model):
    _name = 'sync.store'
    _description = 'Shopify Store Configuration'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name asc'

    name = fields.Char(string='Store Name', required=True, tracking=True)
    store_domain = fields.Char(string='Store Domain', required=True, tracking=True)
    api_version = fields.Char(string='API Version', default='2024-10', required=True)
    api_key = fields.Char(string='API Key')
    api_secret = fields.Char(string='API Secret', password=True)

    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company, tracking=True
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('connected', 'Connected'),
        ('error', 'Connection Error'),
        ('paused', 'Paused'),
    ], string='Status', default='draft', tracking=True)

    # ═══════════════════════════════════════════════════════════════
    # DASHBOARD STATS — Fixed dependencies
    # ═══════════════════════════════════════════════════════════════
    total_sales = fields.Float(
        string='Total Sales', compute='_compute_dashboard_stats',
        store=True, digits=(16, 2)
    )
    total_orders = fields.Integer(
        string='Total Orders', compute='_compute_dashboard_stats', store=True
    )
    total_products = fields.Integer(
        string='Total Products', compute='_compute_dashboard_stats', store=True
    )
    total_customers = fields.Integer(
        string='Total Customers', compute='_compute_dashboard_stats', store=True
    )

    last_catalog_sync = fields.Datetime(string='Last Product Sync')
    last_sale_sync = fields.Datetime(string='Last Order Sync')
    last_contact_sync = fields.Datetime(string='Last Customer Sync')
    color = fields.Integer(string='Color Index', default=0)

    # ═══════════════════════════════════════════════════════════════
    # FIXED: Add related fields as dependencies to force refresh
    # ═══════════════════════════════════════════════════════════════
    @api.depends(
        # Force recompute when any sale changes
        'sale_ids.import_state', 'sale_ids.total_amount',
        # Force recompute when catalog changes
        'catalog_ids.sync_state',
        # Force recompute when contact changes
        'contact_ids.sync_state',
        # Also recompute on name change (for manual refresh)
        'name'
    )
    def _compute_dashboard_stats(self):
        for store in self:
            # Sales calculation
            orders = self.env['sync.sale'].search([
                ('store_id', '=', store.id),
                ('import_state', '=', 'imported')
            ])
            store.total_sales = sum(orders.mapped('total_amount'))
            store.total_orders = len(orders)
            
            # Products
            store.total_products = self.env['sync.catalog'].search_count([
                ('store_id', '=', store.id)
            ])
            
            # Customers
            store.total_customers = self.env['sync.contact'].search_count([
                ('store_id', '=', store.id),
                ('sync_state', '=', 'synced')
            ])

    # ═══════════════════════════════════════════════════════════════
    # RELATION FIELDS — Needed for @api.depends
    # ═══════════════════════════════════════════════════════════════
    sale_ids = fields.One2many('sync.sale', 'store_id', string='Orders')
    catalog_ids = fields.One2many('sync.catalog', 'store_id', string='Products')
    contact_ids = fields.One2many('sync.contact', 'store_id', string='Customers')

    def _get_base_url(self):
        self.ensure_one()
        domain = self.store_domain
        if not domain.endswith('.myshopify.com'):
            domain = "%s.myshopify.com" % domain
        return "https://%s/admin/api/%s" % (domain, self.api_version)

    def _get_headers(self):
        self.ensure_one()
        return {
            'Content-Type': 'application/json',
            'X-Shopify-Access-Token': self.api_secret or ''
        }

    def _api_call(self, endpoint, method='GET', payload=None, params=None):
        self.ensure_one()
        if not self.api_secret:
            return {'success': False, 'error': _('API Secret not configured')}

        url = "%s/%s" % (self._get_base_url(), endpoint)
        _logger.info("Shopify API Call: %s %s", method, url)

        try:
            if method == 'GET':
                response = requests.get(url, headers=self._get_headers(), params=params, timeout=30)
            elif method == 'POST':
                response = requests.post(url, headers=self._get_headers(), json=payload, timeout=30)
            elif method == 'PUT':
                response = requests.put(url, headers=self._get_headers(), json=payload, timeout=30)
            elif method == 'DELETE':
                response = requests.delete(url, headers=self._get_headers(), timeout=30)
            else:
                return {'success': False, 'error': _('Unsupported HTTP method: %s') % method}

            if response.status_code in [200, 201, 204]:
                return {
                    'success': True,
                    'data': response.json() if response.text else {},
                    'status_code': response.status_code
                }
            else:
                error_msg = "HTTP %s: %s" % (response.status_code, response.text)
                _logger.error("Shopify API Error: %s", error_msg)
                return {'success': False, 'error': error_msg}

        except requests.exceptions.Timeout:
            return {'success': False, 'error': _('Request timeout. Please try again.')}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'error': _('Connection error. Check your internet.')}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def action_verify_connection(self):
        self.ensure_one()
        if not self.api_secret:
            raise ValidationError(_('Please configure API Secret first'))

        result = self._api_call('shop.json')

        if result['success']:
            shop_data = result['data'].get('shop', {})
            self.write({
                'state': 'connected',
                'name': shop_data.get('name', self.name),
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connected!'),
                    'message': _("Successfully connected to %s") % shop_data.get('name', self.store_domain),
                    'type': 'success',
                    'sticky': False,
                }
            }
        else:
            self.write({'state': 'error'})
            raise ValidationError(_("Connection failed: %s") % result['error'])

    def action_sync_catalog(self):
        self.ensure_one()
        if self.state != 'connected':
            raise UserError(_('Store must be connected before syncing.'))
        try:
            self.env['sync.catalog'].sync_from_remote(self)
            self.write({'last_catalog_sync': fields.Datetime.now()})
            # Force dashboard refresh
            self._compute_dashboard_stats()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sync Complete'),
                    'message': _('Products synced successfully.'),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            raise UserError(_("Catalog sync failed: %s") % str(e))

    def action_sync_sales(self):
        self.ensure_one()
        if self.state != 'connected':
            raise UserError(_('Store must be connected before syncing.'))
        try:
            self.env['sync.sale'].sync_from_remote(self)
            self.write({'last_sale_sync': fields.Datetime.now()})
            # Force dashboard refresh
            self._compute_dashboard_stats()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sync Complete'),
                    'message': _('Orders synced successfully.'),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            raise UserError(_("Sales sync failed: %s") % str(e))

    def action_sync_contacts(self):
        self.ensure_one()
        if self.state != 'connected':
            raise UserError(_('Store must be connected before syncing.'))
        try:
            self.env['sync.contact'].sync_from_remote(self)
            self.write({'last_contact_sync': fields.Datetime.now()})
            # Force dashboard refresh
            self._compute_dashboard_stats()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sync Complete'),
                    'message': _('Customers synced successfully.'),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            raise UserError(_("Contacts sync failed: %s") % str(e))

    def action_open_dashboard(self):
        self.ensure_one()
        # Force refresh before opening
        self._compute_dashboard_stats()
        try:
            view_id = self.env.ref('bridge_shopify_sync.view_sync_store_dashboard_kanban').id
        except ValueError:
            view_id = False

        return {
            'name': _('Store Dashboard - %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'sync.store',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(view_id, 'form')] if view_id else [(False, 'form')],
            'target': 'current',
            'context': {
                'default_store_id': self.id,
                'create': False,
                'edit': False,
            }
        }

    def action_open_dashboard_kanban(self):
        # Force refresh all stores
        for store in self.search([]):
            store._compute_dashboard_stats()
        
        return {
            'name': _('Shopify Dashboard'),
            'type': 'ir.actions.act_window',
            'res_model': 'sync.store',
            'view_mode': 'kanban,list,form',
            'views': [
                (self.env.ref('bridge_shopify_sync.view_sync_store_dashboard_kanban').id, 'kanban'),
                (False, 'list'),
                (False, 'form'),
            ],
            'target': 'current',
            'context': {'create': False},
        }

    def action_pause_store(self):
        self.ensure_one()
        self.write({'state': 'paused'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Store Paused'),
                'message': _('%s has been paused.') % self.name,
                'type': 'warning',
                'sticky': False,
            }
        }

    def action_resume_store(self):
        self.ensure_one()
        self.write({'state': 'draft'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Store Resumed'),
                'message': _('%s is ready to connect.') % self.name,
                'type': 'info',
                'sticky': False,
            }
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('store_domain'):
                domain = vals['store_domain'].strip().lower()
                if domain.startswith(('http://', 'https://')):
                    domain = domain.split('//')[1].split('/')[0]
                vals['store_domain'] = domain
        return super(SyncStore, self).create(vals_list)

    def write(self, vals):
        if vals.get('store_domain'):
            domain = vals['store_domain'].strip().lower()
            if domain.startswith(('http://', 'https://')):
                domain = domain.split('//')[1].split('/')[0]
            vals['store_domain'] = domain
        return super(SyncStore, self).write(vals)

    def unlink(self):
        for store in self:
            if store.state == 'connected':
                raise UserError(_("Cannot delete a connected store. Pause it first."))
        return super(SyncStore, self).unlink()
