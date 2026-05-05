from odoo import models, fields, api, _
from odoo.exceptions import UserError
import json
import logging
import base64
import requests

_logger = logging.getLogger(__name__)


class SyncContact(models.Model):
    _name = 'sync.contact'
    _description = 'Synchronized Customer Contact'
    _order = 'write_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # IMAGE
    image = fields.Binary(string='Profile Image', attachment=True)
    image_medium = fields.Binary(string='Medium Image', compute='_compute_images', store=True)
    image_small = fields.Binary(string='Small Image', compute='_compute_images', store=True)

    # BASIC INFO
    name = fields.Char(string='Full Name', required=True, index=True, tracking=True)
    remote_id = fields.Char(string='Remote ID', required=True, index=True, readonly=True)
    shopify_customer_id = fields.Char(string='Shopify Customer ID', index=True)
    store_id = fields.Many2one('sync.store', string='Store', required=True, index=True)
    email = fields.Char(string='Email', tracking=True)
    phone = fields.Char(string='Phone', tracking=True)
    company_name = fields.Char(string='Company Name', tracking=True)

    # ADDRESS
    address1 = fields.Char(string='Address Line 1')
    address2 = fields.Char(string='Address Line 2')
    city = fields.Char(string='City')
    province = fields.Char(string='Province/State')
    province_code = fields.Char(string='Province Code')
    country = fields.Char(string='Country')
    country_code = fields.Char(string='Country Code')
    zip = fields.Char(string='ZIP/Postal Code')
    latitude = fields.Float(string='Latitude')
    longitude = fields.Float(string='Longitude')

    # CUSTOMER INFO
    accepts_marketing = fields.Boolean(string='Accepts Marketing', default=False)
    verified_email = fields.Boolean(string='Verified Email', default=False)
    tax_exempt = fields.Boolean(string='Tax Exempt', default=False)
    state = fields.Selection([
        ('disabled', 'Disabled'),
        ('enabled', 'Enabled'),
        ('invited', 'Invited'),
    ], string='Account State', default='enabled', tracking=True)

    # FINANCIAL
    total_spent = fields.Float(string='Total Spent', default=0.0)
    orders_count = fields.Integer(string='Orders Count', default=0)
    currency = fields.Char(string='Currency', default='USD')

    # SYNC INFO
    sync_state = fields.Selection([
        ('pending', 'Pending'),
        ('synced', 'Synced'),
        ('error', 'Error'),
    ], string='Sync State', default='pending', tracking=True)
    last_synced = fields.Datetime(string='Last Synced')
    last_error = fields.Text(string='Last Error')
    sync_direction = fields.Selection([
        ('import', 'Shopify → Odoo'),
        ('export', 'Odoo → Shopify'),
        ('bidirectional', 'Both')
    ], string='Sync Direction', default='import')

    # AUTO SYNC FLAG
    auto_sync_enabled = fields.Boolean(
        string='Auto Sync to Shopify',
        default=True,
        help='Automatically push changes to Shopify when saving in Odoo'
    )

    # NOTES & TAGS
    note = fields.Text(string='Note')
    tags = fields.Char(string='Tags', help='Comma separated tags from Shopify')

    # RAW DATA
    raw_data = fields.Text(string='Raw JSON', help='Complete Shopify API response for debugging')

    # ADDRESSES (One2many)
    address_ids = fields.One2many('sync.contact.address', 'contact_id', string='Addresses')

    @api.depends('image')
    def _compute_images(self):
        for record in self:
            if record.image:
                record.image_medium = record.image
                record.image_small = record.image
            else:
                record.image_medium = False
                record.image_small = False

    # ═══════════════════════════════════════════════════════════════
    # CREATE — Auto sync to Shopify after creation
    # ═══════════════════════════════════════════════════════════════
    @api.model_create_multi
    def create(self, vals_list):
        records = super(SyncContact, self).create(vals_list)
        
        for record in records:
            if record.auto_sync_enabled and record.remote_id and record.store_id:
                try:
                    record._push_to_shopify_silent()
                except Exception as e:
                    _logger.error("Auto sync on create failed for %s: %s", record.name, str(e))
        
        return records

    # ═══════════════════════════════════════════════════════════════
    # WRITE — Auto sync to Shopify after update
    # ═══════════════════════════════════════════════════════════════
    def write(self, vals):
        sync_fields = [
            'name', 'email', 'phone', 'company_name', 'note', 'tags',
            'accepts_marketing', 'tax_exempt', 'state', 'address1',
            'address2', 'city', 'province', 'province_code', 'country',
            'country_code', 'zip', 'phone'
        ]
        
        needs_sync = any(field in vals for field in sync_fields)
        
        result = super(SyncContact, self).write(vals)
        
        if needs_sync:
            for record in self:
                if record.auto_sync_enabled and record.remote_id and record.store_id:
                    try:
                        record._push_to_shopify_silent()
                    except Exception as e:
                        _logger.error("Auto sync on write failed for %s: %s", record.name, str(e))
                        record.write({
                            'sync_state': 'error',
                            'last_error': 'Auto sync failed: %s' % str(e),
                            'last_synced': fields.Datetime.now()
                        })
        
        return result

    # ═══════════════════════════════════════════════════════════════
    # SILENT PUSH — Uses correct 'payload' parameter
    # ═══════════════════════════════════════════════════════════════
    def _push_to_shopify_silent(self):
        """Push to Shopify without raising errors to user"""
        self.ensure_one()
        
        if not self.store_id or not hasattr(self.store_id, '_api_call'):
            _logger.warning("Cannot auto sync: store not configured")
            return False
        
        try:
            name_parts = self.name.split(' ', 1) if self.name else ['', '']
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else ''
            
            payload = {
                'customer': {
                    'first_name': first_name,
                    'last_name': last_name,
                    'email': self.email or '',
                    'phone': self.phone or '',
                    'company_name': self.company_name or '',
                    'note': self.note or '',
                    'tags': self.tags or '',
                    'accepts_marketing': self.accepts_marketing,
                    'tax_exempt': self.tax_exempt,
                }
            }
            
            _logger.info("Auto pushing contact %s to Shopify", self.name)
            
            # CORRECT: Use 'payload' parameter as per _api_call signature
            result = self.store_id._api_call(
                'customers/%s.json' % self.remote_id,
                method='PUT',
                payload=payload
            )
            
            _logger.info("API result: %s", result)
            
            if result.get('success'):
                self.write({
                    'sync_state': 'synced',
                    'last_synced': fields.Datetime.now(),
                    'last_error': False
                })
                _logger.info("Auto sync successful for %s", self.name)
                return True
            else:
                error_msg = result.get('error', 'Unknown API error')
                self.write({
                    'sync_state': 'error',
                    'last_error': error_msg,
                    'last_synced': fields.Datetime.now()
                })
                _logger.error("Auto sync failed for %s: %s", self.name, error_msg)
                return False
                
        except Exception as e:
            self.write({
                'sync_state': 'error',
                'last_error': str(e),
                'last_synced': fields.Datetime.now()
            })
            _logger.error("Auto sync exception for %s: %s", self.name, str(e))
            return False

    # ═══════════════════════════════════════════════════════════════
    # MANUAL SYNC BUTTONS
    # ═══════════════════════════════════════════════════════════════
    def action_sync_from_shopify(self):
        self.ensure_one()
        if not self.store_id:
            raise UserError(_('No store configured for this contact!'))
        
        try:
            _logger.info("Manual sync from Shopify for %s", self.name)
            result = self.store_id._api_call('customers/%s.json' % self.remote_id)
            
            if not result.get('success'):
                error_msg = result.get('error', 'Unknown API error')
                self.write({
                    'sync_state': 'error',
                    'last_error': error_msg,
                    'last_synced': fields.Datetime.now()
                })
                raise UserError(_('Sync from Shopify failed: %s') % error_msg)
            
            customer = result.get('data', {}).get('customer', {})
            if not customer:
                raise UserError(_('No customer data received from Shopify'))
            
            self._update_from_shopify_data(customer)
            self.write({
                'sync_state': 'synced',
                'last_synced': fields.Datetime.now(),
                'last_error': False
            })
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Customer synced from Shopify successfully'),
                    'type': 'success',
                    'sticky': False,
                }
            }
            
        except Exception as e:
            error_msg = str(e)
            _logger.error("Manual sync from Shopify error: %s", error_msg)
            self.write({
                'sync_state': 'error',
                'last_error': error_msg,
                'last_synced': fields.Datetime.now()
            })
            raise UserError(_('Sync from Shopify failed: %s') % error_msg)

    def action_sync_to_shopify(self):
        self.ensure_one()
        if not self.store_id:
            raise UserError(_('No store configured for this contact!'))
        
        try:
            success = self._push_to_shopify_silent()
            if success:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Customer synced to Shopify successfully'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError(_('Sync to Shopify failed. Check Last Error field for details.'))
        except Exception as e:
            raise UserError(_('Sync to Shopify failed: %s') % str(e))

    def action_view_in_shopify(self):
        self.ensure_one()
        if self.store_id and self.remote_id:
            shop_url = self.store_id.shop_url or ''
            if not shop_url.startswith('http'):
                shop_url = 'https://' + shop_url
            url = "%s/admin/customers/%s" % (shop_url.rstrip('/'), self.remote_id)
            return {
                'type': 'ir.actions.act_url',
                'url': url,
                'target': 'new',
            }
        raise UserError(_('Shopify URL not available!'))

    def _update_from_shopify_data(self, customer):
        self.ensure_one()
        
        first_name = customer.get('first_name', '')
        last_name = customer.get('last_name', '')
        full_name = ('%s %s' % (first_name, last_name)).strip() or customer.get('email', 'Unknown')
        
        vals = {
            'name': full_name,
            'shopify_customer_id': str(customer.get('id', '')),
            'email': customer.get('email', ''),
            'phone': customer.get('phone', ''),
            'company_name': customer.get('company_name', ''),
            'accepts_marketing': customer.get('accepts_marketing', False),
            'verified_email': customer.get('verified_email', False),
            'tax_exempt': customer.get('tax_exempt', False),
            'state': customer.get('state', 'enabled'),
            'total_spent': float(customer.get('total_spent', 0) or 0),
            'orders_count': customer.get('orders_count', 0),
            'currency': customer.get('currency', 'USD'),
            'note': customer.get('note', ''),
            'tags': customer.get('tags', ''),
            'raw_data': json.dumps(customer, indent=2),
        }

        addresses = customer.get('addresses', [])
        if addresses:
            default_addr = addresses[0]
            vals.update({
                'address1': default_addr.get('address1', ''),
                'address2': default_addr.get('address2', ''),
                'city': default_addr.get('city', ''),
                'province': default_addr.get('province', ''),
                'province_code': default_addr.get('province_code', ''),
                'country': default_addr.get('country', ''),
                'country_code': default_addr.get('country_code', ''),
                'zip': default_addr.get('zip', ''),
                'latitude': float(default_addr.get('latitude', 0) or 0),
                'longitude': float(default_addr.get('longitude', 0) or 0),
            })

            for addr in addresses:
                existing = self.env['sync.contact.address'].search([
                    ('contact_id', '=', self.id),
                    ('remote_id', '=', str(addr.get('id', '')))
                ], limit=1)
                
                addr_val = {
                    'contact_id': self.id,
                    'remote_id': str(addr.get('id', '')),
                    'address1': addr.get('address1', ''),
                    'address2': addr.get('address2', ''),
                    'city': addr.get('city', ''),
                    'province': addr.get('province', ''),
                    'province_code': addr.get('province_code', ''),
                    'country': addr.get('country', ''),
                    'country_code': addr.get('country_code', ''),
                    'zip': addr.get('zip', ''),
                    'phone': addr.get('phone', ''),
                    'default': addr.get('default', False),
                }
                
                if existing:
                    existing.write(addr_val)
                else:
                    self.env['sync.contact.address'].create(addr_val)

        self.write(vals)

    @api.model
    def sync_from_remote(self, store, since_date=None):
        journal = self.env['sync.journal'].create({
            'name': 'Contact Pull: %s' % store.name,
            'store_id': store.id,
            'direction': 'inbound',
            'target': 'contact',
            'state': 'running',
            'total': 0,
            'passed': 0,
            'failed': 0,
        })
        
        try:
            params = {'limit': 250}
            if since_date:
                params['updated_at_min'] = since_date.isoformat()
            
            result = store._api_call('customers.json', params=params)
            
            if not result.get('success'):
                journal.write({'state': 'failed', 'error': result.get('error', 'Unknown error')})
                return False
            
            customers = result.get('data', {}).get('customers', [])
            journal.write({'total': len(customers)})
            
            for customer in customers:
                try:
                    remote_id = str(customer.get('id', ''))
                    existing = self.search([
                        ('remote_id', '=', remote_id),
                        ('store_id', '=', store.id)
                    ], limit=1)
                    
                    if existing:
                        existing._update_from_shopify_data(customer)
                        existing.write({'sync_state': 'synced'})
                    else:
                        first_name = customer.get('first_name', '')
                        last_name = customer.get('last_name', '')
                        full_name = ('%s %s' % (first_name, last_name)).strip() or customer.get('email', 'Unknown')
                        
                        new_contact = self.create({
                            'name': full_name,
                            'remote_id': remote_id,
                            'store_id': store.id,
                            'sync_state': 'synced',
                        })
                        new_contact._update_from_shopify_data(customer)
                    
                    journal.passed += 1
                    
                except Exception as e:
                    journal.failed += 1
                    _logger.error("Contact sync error for customer %s: %s", customer.get('id', 'unknown'), str(e))
            
            journal.write({'state': 'done'})
            return True
            
        except Exception as e:
            journal.write({'state': 'failed', 'error': str(e)})
            return False


class SyncContactAddress(models.Model):
    _name = 'sync.contact.address'
    _description = 'Customer Address'
    _order = 'default desc, id'

    contact_id = fields.Many2one('sync.contact', string='Contact', required=True, ondelete='cascade', index=True)
    remote_id = fields.Char(string='Remote Address ID', index=True)
    address1 = fields.Char(string='Address Line 1')
    address2 = fields.Char(string='Address Line 2')
    city = fields.Char(string='City')
    province = fields.Char(string='Province/State')
    province_code = fields.Char(string='Province Code')
    country = fields.Char(string='Country')
    country_code = fields.Char(string='Country Code')
    zip = fields.Char(string='ZIP/Postal Code')
    phone = fields.Char(string='Phone')
    default = fields.Boolean(string='Default Address', default=False)
