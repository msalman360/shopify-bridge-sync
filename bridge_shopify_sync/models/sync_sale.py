from odoo import models, fields, api, _
from odoo.exceptions import UserError
import json
import logging
import base64
import requests

_logger = logging.getLogger(__name__)


class SyncSale(models.Model):
    _name = 'sync.sale'
    _description = 'Synchronized Sales Order'
    _order = 'order_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # ═══════════════════════════════════════════════════════════════
    # BASIC INFO
    # ═══════════════════════════════════════════════════════════════
    name = fields.Char(string='Order Number', required=True, index=True, tracking=True)
    remote_id = fields.Char(string='Remote ID', required=True, index=True, readonly=True)
    shopify_order_id = fields.Char(string='Shopify Order ID', index=True)
    store_id = fields.Many2one('sync.store', string='Store', required=True, index=True)
    
    # ORDER STATUS
    order_status = fields.Selection([
        ('open', 'Open'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
        ('any', 'Any')
    ], string='Order Status', default='open', tracking=True)
    financial_status = fields.Selection([
        ('pending', 'Pending'),
        ('authorized', 'Authorized'),
        ('partially_paid', 'Partially Paid'),
        ('paid', 'Paid'),
        ('partially_refunded', 'Partially Refunded'),
        ('refunded', 'Refunded'),
        ('voided', 'Voided'),
        ('unpaid', 'Unpaid'),
        ('any', 'Any')
    ], string='Financial Status', default='any', tracking=True)
    fulfillment_status = fields.Selection([
        ('fulfilled', 'Fulfilled'),
        ('partial', 'Partial'),
        ('restocked', 'Restocked'),
        ('pending', 'Pending'),
        ('any', 'Any')
    ], string='Fulfillment Status', default='any', tracking=True)

    # ═══════════════════════════════════════════════════════════════
    # CUSTOMER INFO
    # ═══════════════════════════════════════════════════════════════
    customer_name = fields.Char(string='Customer Name', tracking=True)
    customer_email = fields.Char(string='Customer Email', tracking=True)
    customer_phone = fields.Char(string='Customer Phone')
    customer_id = fields.Many2one('sync.contact', string='Customer')

    # ═══════════════════════════════════════════════════════════════
    # PRICING
    # ═══════════════════════════════════════════════════════════════
    subtotal_price = fields.Float(string='Subtotal')
    total_tax = fields.Float(string='Total Tax')
    total_discounts = fields.Float(string='Total Discounts')
    total_shipping = fields.Float(string='Shipping Cost')
    total_amount = fields.Float(string='Total Amount', tracking=True)
    currency = fields.Char(string='Currency', default='USD')

    # ═══════════════════════════════════════════════════════════════
    # SHIPPING ADDRESS
    # ═══════════════════════════════════════════════════════════════
    shipping_name = fields.Char(string='Shipping Name')
    shipping_address1 = fields.Char(string='Shipping Address 1')
    shipping_address2 = fields.Char(string='Shipping Address 2')
    shipping_city = fields.Char(string='Shipping City')
    shipping_province = fields.Char(string='Shipping Province')
    shipping_zip = fields.Char(string='Shipping ZIP')
    shipping_country = fields.Char(string='Shipping Country')
    shipping_phone = fields.Char(string='Shipping Phone')

    # ═══════════════════════════════════════════════════════════════
    # BILLING ADDRESS
    # ═══════════════════════════════════════════════════════════════
    billing_name = fields.Char(string='Billing Name')
    billing_address1 = fields.Char(string='Billing Address 1')
    billing_address2 = fields.Char(string='Billing Address 2')
    billing_city = fields.Char(string='Billing City')
    billing_province = fields.Char(string='Billing Province')
    billing_zip = fields.Char(string='Billing ZIP')
    billing_country = fields.Char(string='Billing Country')

    # ═══════════════════════════════════════════════════════════════
    # ORDER DETAILS
    # ═══════════════════════════════════════════════════════════════
    order_date = fields.Datetime(string='Order Date')
    processed_at = fields.Datetime(string='Processed At')
    cancelled_at = fields.Datetime(string='Cancelled At')
    cancel_reason = fields.Char(string='Cancel Reason')
    note = fields.Text(string='Order Note')
    tags = fields.Char(string='Tags')
    source_name = fields.Char(string='Source')

    # ═══════════════════════════════════════════════════════════════
    # SYNC INFO
    # ═══════════════════════════════════════════════════════════════
    import_state = fields.Selection([
        ('new', 'New'),
        ('imported', 'Imported'),
        ('error', 'Error'),
    ], string='Import State', default='new', tracking=True)
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

    # RAW DATA
    raw_data = fields.Text(string='Raw JSON', help='Complete Shopify API response for debugging')

    # RELATIONS
    line_ids = fields.One2many('sync.sale.line', 'order_id', string='Order Lines')
    fulfillment_ids = fields.One2many('sync.sale.fulfillment', 'order_id', string='Fulfillments')

    # ═══════════════════════════════════════════════════════════════
    # COMPUTE
    # ═══════════════════════════════════════════════════════════════
    @api.depends('line_ids')
    def _compute_line_count(self):
        for order in self:
            order.line_count = len(order.line_ids)

    line_count = fields.Integer(string='Line Count', compute='_compute_line_count', store=True)

    # ═══════════════════════════════════════════════════════════════
    # CREATE — Auto sync to Shopify after creation
    # ═══════════════════════════════════════════════════════════════
    @api.model_create_multi
    def create(self, vals_list):
        records = super(SyncSale, self).create(vals_list)
        
        for record in records:
            if record.auto_sync_enabled and record.remote_id and record.store_id:
                try:
                    record._push_to_shopify_silent()
                except Exception as e:
                    _logger.error("Auto sync on create failed for order %s: %s", record.name, str(e))
        
        return records

    # ═══════════════════════════════════════════════════════════════
    # WRITE — Auto sync to Shopify after update
    # ═══════════════════════════════════════════════════════════════
    def write(self, vals):
        # FIXED: Added 'order_status' and 'cancel_reason' to sync_fields
        sync_fields = [
            'name', 'order_status', 'financial_status', 'fulfillment_status',
            'note', 'tags', 'customer_name', 'customer_email', 'customer_phone',
            'shipping_name', 'shipping_address1', 'shipping_city', 'shipping_province',
            'shipping_zip', 'shipping_country', 'shipping_phone', 'cancel_reason'
        ]
        
        needs_sync = any(field in vals for field in sync_fields)
        
        result = super(SyncSale, self).write(vals)
        
        if needs_sync:
            for record in self:
                if record.auto_sync_enabled and record.remote_id and record.store_id:
                    try:
                        record._push_to_shopify_silent()
                    except Exception as e:
                        _logger.error("Auto sync on write failed for order %s: %s", record.name, str(e))
                        record.write({
                            'sync_state': 'error',
                            'last_error': 'Auto sync failed: %s' % str(e),
                            'last_synced': fields.Datetime.now()
                        })
        
        return result

    # ═══════════════════════════════════════════════════════════════
    # SILENT PUSH — Handles cancel via proper API endpoint
    # ═══════════════════════════════════════════════════════════════
    def _push_to_shopify_silent(self):
        """Push order to Shopify without raising errors to user"""
        self.ensure_one()
        
        if not self.store_id or not hasattr(self.store_id, '_api_call'):
            _logger.warning("Cannot auto sync: store not configured")
            return False
        
        try:
            # ═══════════════════════════════════════════════════════════════
            # HANDLE CANCEL — Shopify requires DELETE endpoint for cancel
            # ═══════════════════════════════════════════════════════════════
            if self.order_status == 'cancelled':
                _logger.info("Cancelling order %s in Shopify", self.name)
                
                result = self.store_id._api_call(
                    'orders/%s/cancel.json' % self.remote_id,
                    method='POST',
                    payload={'reason': self.cancel_reason or 'Customer request'}
                )
                
                _logger.info("Cancel API result: %s", result)
                
                if result.get('success'):
                    self.write({
                        'sync_state': 'synced',
                        'last_synced': fields.Datetime.now(),
                        'last_error': False
                    })
                    return True
                else:
                    error_msg = result.get('error', 'Unknown API error')
                    self.write({
                        'sync_state': 'error',
                        'last_error': error_msg,
                        'last_synced': fields.Datetime.now()
                    })
                    return False
            
            # ═══════════════════════════════════════════════════════════════
            # NORMAL UPDATE — PUT for other changes
            # ═══════════════════════════════════════════════════════════════
            payload = {
                'order': {
                    'note': self.note or '',
                    'tags': self.tags or '',
                }
            }
            
            _logger.info("Auto pushing order %s to Shopify", self.name)
            
            result = self.store_id._api_call(
                'orders/%s.json' % self.remote_id,
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
                _logger.info("Auto sync successful for order %s", self.name)
                return True
            else:
                error_msg = result.get('error', 'Unknown API error')
                self.write({
                    'sync_state': 'error',
                    'last_error': error_msg,
                    'last_synced': fields.Datetime.now()
                })
                _logger.error("Auto sync failed for order %s: %s", self.name, error_msg)
                return False
                
        except Exception as e:
            self.write({
                'sync_state': 'error',
                'last_error': str(e),
                'last_synced': fields.Datetime.now()
            })
            _logger.error("Auto sync exception for order %s: %s", self.name, str(e))
            return False

    # ═══════════════════════════════════════════════════════════════
    # MANUAL CANCEL BUTTON
    # ═══════════════════════════════════════════════════════════════
    def action_cancel_order(self):
        """Cancel order in both Odoo and Shopify"""
        self.ensure_one()
        
        # Update local status
        self.write({
            'order_status': 'cancelled',
            'cancel_reason': 'Cancelled by user',
            'cancelled_at': fields.Datetime.now()
        })
        
        # Push to Shopify
        if self.auto_sync_enabled and self.remote_id and self.store_id:
            try:
                result = self._push_to_shopify_silent()
                if result:
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Success'),
                            'message': _('Order cancelled in Shopify'),
                            'type': 'success',
                            'sticky': False,
                        }
                    }
                else:
                    raise UserError(_('Failed to cancel order in Shopify. Check Last Error.'))
            except Exception as e:
                raise UserError(_('Cancel failed: %s') % str(e))
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Local Cancel'),
                'message': _('Order cancelled locally only. Auto sync is disabled.'),
                'type': 'warning',
                'sticky': False,
            }
        }

    # ═══════════════════════════════════════════════════════════════
    # MANUAL SYNC BUTTONS
    # ═══════════════════════════════════════════════════════════════
    def action_sync_from_shopify(self):
        """Pull latest order data from Shopify"""
        self.ensure_one()
        if not self.store_id:
            raise UserError(_('No store configured for this order!'))
        
        try:
            _logger.info("Manual sync from Shopify for order %s", self.name)
            result = self.store_id._api_call('orders/%s.json' % self.remote_id)
            
            if not result.get('success'):
                error_msg = result.get('error', 'Unknown API error')
                self.write({
                    'sync_state': 'error',
                    'last_error': error_msg,
                    'last_synced': fields.Datetime.now()
                })
                raise UserError(_('Sync from Shopify failed: %s') % error_msg)
            
            order = result.get('data', {}).get('order', {})
            if not order:
                raise UserError(_('No order data received from Shopify'))
            
            self._update_from_shopify_data(order)
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
                    'message': _('Order synced from Shopify successfully'),
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
        """Manual push to Shopify with user feedback"""
        self.ensure_one()
        if not self.store_id:
            raise UserError(_('No store configured for this order!'))
        
        try:
            success = self._push_to_shopify_silent()
            if success:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Order synced to Shopify successfully'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError(_('Sync to Shopify failed. Check Last Error field for details.'))
        except Exception as e:
            raise UserError(_('Sync to Shopify failed: %s') % str(e))

    def action_view_in_shopify(self):
        """Open order in Shopify admin"""
        self.ensure_one()
        if self.store_id and self.remote_id:
            shop_url = self.store_id.shop_url or ''
            if not shop_url.startswith('http'):
                shop_url = 'https://' + shop_url
            url = "%s/admin/orders/%s" % (shop_url.rstrip('/'), self.remote_id)
            return {
                'type': 'ir.actions.act_url',
                'url': url,
                'target': 'new',
            }
        raise UserError(_('Shopify URL not available!'))

    def _update_from_shopify_data(self, order):
        """Update record from Shopify API order dict"""
        self.ensure_one()
        
        # Customer info
        customer = order.get('customer', {})
        customer_name = ''
        if customer:
            first_name = customer.get('first_name', '')
            last_name = customer.get('last_name', '')
            customer_name = ('%s %s' % (first_name, last_name)).strip()
            customer_email = customer.get('email', '')
            customer_phone = customer.get('phone', '')
            
            # Find or create customer link
            contact = self.env['sync.contact'].search([
                ('remote_id', '=', str(customer.get('id', ''))),
                ('store_id', '=', self.store_id.id)
            ], limit=1)
        else:
            customer_name = order.get('billing_address', {}).get('name', '')
            customer_email = ''
            customer_phone = ''
            contact = False

        # Shipping address
        shipping = order.get('shipping_address', {})
        
        # Billing address
        billing = order.get('billing_address', {})
        
        vals = {
            'name': "#%s" % order.get('order_number', order.get('id', '')),
            'shopify_order_id': str(order.get('id', '')),
            'order_status': order.get('status', 'open'),
            'financial_status': order.get('financial_status', 'any'),
            'fulfillment_status': order.get('fulfillment_status', 'any'),
            'customer_name': customer_name,
            'customer_email': customer_email,
            'customer_phone': customer_phone,
            'customer_id': contact.id if contact else False,
            'subtotal_price': float(order.get('subtotal_price', 0) or 0),
            'total_tax': float(order.get('total_tax', 0) or 0),
            'total_discounts': float(order.get('total_discounts', 0) or 0),
            'total_shipping': float(order.get('total_shipping_price_set', {}).get('shop_money', {}).get('amount', 0) or 0),
            'total_amount': float(order.get('total_price', 0) or 0),
            'currency': order.get('currency', 'USD'),
            'shipping_name': shipping.get('name', ''),
            'shipping_address1': shipping.get('address1', ''),
            'shipping_address2': shipping.get('address2', ''),
            'shipping_city': shipping.get('city', ''),
            'shipping_province': shipping.get('province', ''),
            'shipping_zip': shipping.get('zip', ''),
            'shipping_country': shipping.get('country', ''),
            'shipping_phone': shipping.get('phone', ''),
            'billing_name': billing.get('name', ''),
            'billing_address1': billing.get('address1', ''),
            'billing_address2': billing.get('address2', ''),
            'billing_city': billing.get('city', ''),
            'billing_province': billing.get('province', ''),
            'billing_zip': billing.get('zip', ''),
            'billing_country': billing.get('country', ''),
            'order_date': order.get('created_at'),
            'processed_at': order.get('processed_at'),
            'cancelled_at': order.get('cancelled_at'),
            'cancel_reason': order.get('cancel_reason', ''),
            'note': order.get('note', ''),
            'tags': order.get('tags', ''),
            'source_name': order.get('source_name', ''),
            'import_state': 'imported',
            'raw_data': json.dumps(order, indent=2),
        }
        
        # Order lines
        line_vals = []
        for line in order.get('line_items', []):
            existing = self.env['sync.sale.line'].search([
                ('order_id', '=', self.id),
                ('remote_id', '=', str(line.get('id', '')))
            ], limit=1)
            
            line_val = {
                'order_id': self.id,
                'remote_id': str(line.get('id', '')),
                'title': line.get('title', '') or line.get('name', ''),
                'quantity': line.get('quantity', 0),
                'price': float(line.get('price', 0) or 0),
                'sku': line.get('sku', ''),
                'variant_id': str(line.get('variant_id', '')),
                'product_id': str(line.get('product_id', '')),
                'fulfillment_status': line.get('fulfillment_status', ''),
            }
            
            if existing:
                existing.write(line_val)
            else:
                line_vals.append(line_val)
        
        if line_vals:
            self.env['sync.sale.line'].create(line_vals)
        
        # Fulfillments
        fulfill_vals = []
        for fulfill in order.get('fulfillments', []):
            existing = self.env['sync.sale.fulfillment'].search([
                ('order_id', '=', self.id),
                ('remote_id', '=', str(fulfill.get('id', '')))
            ], limit=1)
            
            fulfill_val = {
                'order_id': self.id,
                'remote_id': str(fulfill.get('id', '')),
                'status': fulfill.get('status', ''),
                'tracking_number': fulfill.get('tracking_number', ''),
                'tracking_company': fulfill.get('tracking_company', ''),
                'shipment_status': fulfill.get('shipment_status', ''),
            }
            
            if existing:
                existing.write(fulfill_val)
            else:
                fulfill_vals.append(fulfill_val)
        
        if fulfill_vals:
            self.env['sync.sale.fulfillment'].create(fulfill_vals)
        
        self.write(vals)

    @api.model
    def sync_from_remote(self, store, date_from=None, date_to=None):
        """Bulk sync orders from Shopify"""
        journal = self.env['sync.journal'].create({
            'name': 'Sale Pull: %s' % store.name,
            'store_id': store.id,
            'direction': 'inbound',
            'target': 'sale',
            'state': 'running',
            'total': 0,
            'passed': 0,
            'failed': 0,
        })
        
        try:
            params = {'status': 'any', 'limit': 250}
            if date_from:
                params['created_at_min'] = date_from
            if date_to:
                params['created_at_max'] = date_to
            
            result = store._api_call('orders.json', params=params)
            
            if not result.get('success'):
                journal.write({'state': 'failed', 'error': result.get('error', 'Unknown error')})
                return False
            
            orders = result.get('data', {}).get('orders', [])
            journal.write({'total': len(orders)})
            
            for order in orders:
                try:
                    remote_id = str(order.get('id', ''))
                    existing = self.search([
                        ('remote_id', '=', remote_id),
                        ('store_id', '=', store.id)
                    ], limit=1)
                    
                    if existing:
                        existing._update_from_shopify_data(order)
                        existing.write({'sync_state': 'synced'})
                    else:
                        new_order = self.create({
                            'name': "#%s" % order.get('order_number', order.get('id', '')),
                            'remote_id': remote_id,
                            'store_id': store.id,
                            'import_state': 'imported',
                            'sync_state': 'synced',
                        })
                        new_order._update_from_shopify_data(order)
                    
                    journal.passed += 1
                    
                except Exception as e:
                    journal.failed += 1
                    _logger.error("Sale sync error for order %s: %s", order.get('id', 'unknown'), str(e))
            
            journal.write({'state': 'done'})
            return True
            
        except Exception as e:
            journal.write({'state': 'failed', 'error': str(e)})
            return False


class SyncSaleLine(models.Model):
    _name = 'sync.sale.line'
    _description = 'Sale Order Line'
    _order = 'id'

    order_id = fields.Many2one('sync.sale', string='Order', required=True, ondelete='cascade', index=True)
    remote_id = fields.Char(string='Remote Line ID', index=True)
    title = fields.Char(string='Product')
    sku = fields.Char(string='SKU')
    quantity = fields.Integer(string='Qty')
    price = fields.Float(string='Price')
    variant_id = fields.Char(string='Variant ID')
    product_id = fields.Char(string='Product ID')
    fulfillment_status = fields.Char(string='Fulfillment Status')


class SyncSaleFulfillment(models.Model):
    _name = 'sync.sale.fulfillment'
    _description = 'Order Fulfillment'
    _order = 'id'

    order_id = fields.Many2one('sync.sale', string='Order', required=True, ondelete='cascade', index=True)
    remote_id = fields.Char(string='Remote Fulfillment ID', index=True)
    status = fields.Char(string='Status')
    tracking_number = fields.Char(string='Tracking #')
    tracking_company = fields.Char(string='Carrier')
    shipment_status = fields.Char(string='Shipment Status')
