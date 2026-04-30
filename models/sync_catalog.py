from odoo import models, fields, api, _
from odoo.exceptions import UserError
import json
import logging
import base64
import requests

_logger = logging.getLogger(__name__)


class SyncCatalog(models.Model):
    _name = 'sync.catalog'
    _description = 'Synchronized Product Catalog'
    _order = 'write_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # ═══════════════════════════════════════════════════════════════
    # IMAGES
    # ═══════════════════════════════════════════════════════════════
    image = fields.Binary(string='Main Image', attachment=True, help='Product main image')
    image_medium = fields.Binary(string='Medium Image', compute='_compute_images', store=True)
    image_small = fields.Binary(string='Small Image', compute='_compute_images', store=True)

    # ═══════════════════════════════════════════════════════════════
    # BASIC INFO
    # ═══════════════════════════════════════════════════════════════
    name = fields.Char(string='Title', required=True, index=True, tracking=True)
    remote_id = fields.Char(string='Remote ID', required=True, index=True, readonly=True)
    shopify_product_id = fields.Char(string='Shopify Product ID', index=True)
    store_id = fields.Many2one('sync.store', string='Store', required=True, index=True)
    product_type = fields.Char(string='Product Type', tracking=True)
    vendor = fields.Char(string='Vendor', tracking=True)
    tags = fields.Char(string='Tags', help='Comma separated tags from Shopify')
    status = fields.Selection([
        ('active', 'Active'),
        ('draft', 'Draft'),
        ('archived', 'Archived')
    ], string='Status', default='active', tracking=True)

    # ═══════════════════════════════════════════════════════════════
    # PRICING
    # ═══════════════════════════════════════════════════════════════
    price = fields.Float(string='Price', tracking=True)
    compare_at_price = fields.Float(string='Compare at Price', help='Original price before discount')
    cost_price = fields.Float(string='Cost Price', help='Your purchase cost')
    currency = fields.Char(string='Currency', default='USD')

    # ═══════════════════════════════════════════════════════════════
    # INVENTORY
    # ═══════════════════════════════════════════════════════════════
    sku = fields.Char(string='SKU', index=True)
    barcode = fields.Char(string='Barcode')
    inventory_quantity = fields.Integer(string='Inventory Quantity', default=0)
    inventory_policy = fields.Selection([
        ('deny', 'Deny'),
        ('continue', 'Continue')
    ], string='Inventory Policy', default='deny')
    fulfillment_service = fields.Char(string='Fulfillment Service', default='manual')
    weight = fields.Float(string='Weight')
    weight_unit = fields.Selection([
        ('kg', 'kg'),
        ('g', 'g'),
        ('lb', 'lb'),
        ('oz', 'oz')
    ], string='Weight Unit', default='kg')

    # ═══════════════════════════════════════════════════════════════
    # SYNC INFO
    # ═══════════════════════════════════════════════════════════════
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

    # ═══════════════════════════════════════════════════════════════
    # CONTENT / SEO
    # ═══════════════════════════════════════════════════════════════
    description = fields.Html(string='Description', sanitize=True)
    seo_title = fields.Char(string='SEO Title')
    seo_description = fields.Text(string='SEO Description')
    handle = fields.Char(string='Handle', help='URL-friendly product handle')
    slug = fields.Char(string='Slug')

    # ═══════════════════════════════════════════════════════════════
    # RAW DATA & RELATIONS
    # ═══════════════════════════════════════════════════════════════
    raw_data = fields.Text(string='Raw JSON', help='Complete Shopify API response for debugging')
    variant_ids = fields.One2many('sync.catalog.variant', 'catalog_id', string='Variants')
    image_ids = fields.One2many('sync.catalog.image', 'catalog_id', string='Gallery Images')

    # ═══════════════════════════════════════════════════════════════
    # COMPUTE METHODS
    # ═══════════════════════════════════════════════════════════════
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
        records = super(SyncCatalog, self).create(vals_list)
        
        for record in records:
            if record.auto_sync_enabled and record.remote_id and record.store_id:
                try:
                    record._push_to_shopify_silent()
                except Exception as e:
                    _logger.error("Auto sync on create failed for product %s: %s", record.name, str(e))
        
        return records

    # ═══════════════════════════════════════════════════════════════
    # WRITE — Auto sync to Shopify after update
    # ═══════════════════════════════════════════════════════════════
    def write(self, vals):
        sync_fields = [
            'name', 'description', 'product_type', 'vendor', 'tags', 'status',
            'price', 'compare_at_price', 'cost_price', 'sku', 'barcode',
            'inventory_quantity', 'inventory_policy', 'fulfillment_service',
            'weight', 'weight_unit', 'seo_title', 'seo_description', 'handle', 'slug'
        ]
        
        needs_sync = any(field in vals for field in sync_fields)
        
        result = super(SyncCatalog, self).write(vals)
        
        if needs_sync:
            for record in self:
                if record.auto_sync_enabled and record.remote_id and record.store_id:
                    try:
                        record._push_to_shopify_silent()
                    except Exception as e:
                        _logger.error("Auto sync on write failed for product %s: %s", record.name, str(e))
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
        """Push product to Shopify without raising errors to user"""
        self.ensure_one()
        
        if not self.store_id or not hasattr(self.store_id, '_api_call'):
            _logger.warning("Cannot auto sync: store not configured")
            return False
        
        try:
            payload = {
                'product': {
                    'title': self.name,
                    'body_html': self.description or '',
                    'vendor': self.vendor or '',
                    'product_type': self.product_type or '',
                    'tags': self.tags or '',
                    'status': self.status,
                }
            }
            
            _logger.info("Auto pushing product %s to Shopify", self.name)
            
            # Use 'payload' parameter as per _api_call signature
            result = self.store_id._api_call(
                'products/%s.json' % self.remote_id,
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
                _logger.info("Auto sync successful for product %s", self.name)
                return True
            else:
                error_msg = result.get('error', 'Unknown API error')
                self.write({
                    'sync_state': 'error',
                    'last_error': error_msg,
                    'last_synced': fields.Datetime.now()
                })
                _logger.error("Auto sync failed for product %s: %s", self.name, error_msg)
                return False
                
        except Exception as e:
            self.write({
                'sync_state': 'error',
                'last_error': str(e),
                'last_synced': fields.Datetime.now()
            })
            _logger.error("Auto sync exception for product %s: %s", self.name, str(e))
            return False

    # ═══════════════════════════════════════════════════════════════
    # MANUAL SYNC BUTTONS
    # ═══════════════════════════════════════════════════════════════
    def action_sync_from_shopify(self):
        """Pull latest product data from Shopify"""
        self.ensure_one()
        if not self.store_id:
            raise UserError(_('No store configured for this product!'))
        
        try:
            _logger.info("Manual sync from Shopify for product %s", self.name)
            result = self.store_id._api_call('products/%s.json' % self.remote_id)
            
            if not result.get('success'):
                error_msg = result.get('error', 'Unknown API error')
                self.write({
                    'sync_state': 'error',
                    'last_error': error_msg,
                    'last_synced': fields.Datetime.now()
                })
                raise UserError(_('Sync from Shopify failed: %s') % error_msg)
            
            product = result.get('data', {}).get('product', {})
            if not product:
                raise UserError(_('No product data received from Shopify'))
            
            self._update_from_shopify_data(product)
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
                    'message': _('Product synced from Shopify successfully'),
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
            raise UserError(_('No store configured for this product!'))
        
        try:
            success = self._push_to_shopify_silent()
            if success:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Product synced to Shopify successfully'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError(_('Sync to Shopify failed. Check Last Error field for details.'))
        except Exception as e:
            raise UserError(_('Sync to Shopify failed: %s') % str(e))

    def action_view_in_shopify(self):
        """Open product in Shopify admin"""
        self.ensure_one()
        if self.store_id and self.handle:
            shop_url = self.store_id.shop_url or ''
            if not shop_url.startswith('http'):
                shop_url = 'https://' + shop_url
            url = "%s/admin/products/%s" % (shop_url.rstrip('/'), self.remote_id)
            return {
                'type': 'ir.actions.act_url',
                'url': url,
                'target': 'new',
            }
        raise UserError(_('Shopify URL not available!'))

    def _update_from_shopify_data(self, product):
        """Update record from Shopify API product dict"""
        self.ensure_one()
        
        vals = {
            'name': product.get('title', self.name),
            'shopify_product_id': str(product.get('id', '')),
            'product_type': product.get('product_type', ''),
            'vendor': product.get('vendor', ''),
            'tags': product.get('tags', ''),
            'status': product.get('status', 'active'),
            'description': product.get('body_html', ''),
            'handle': product.get('handle', ''),
            'seo_title': product.get('seo_title', ''),
            'seo_description': product.get('seo_description', ''),
            'raw_data': json.dumps(product, indent=2),
        }
        
        # Pricing from first variant
        variants = product.get('variants', [])
        if variants:
            first = variants[0]
            vals.update({
                'price': float(first.get('price', 0) or 0),
                'compare_at_price': float(first.get('compare_at_price', 0) or 0),
                'sku': first.get('sku', ''),
                'barcode': first.get('barcode', ''),
                'inventory_quantity': first.get('inventory_quantity', 0),
                'inventory_policy': first.get('inventory_policy', 'deny'),
                'fulfillment_service': first.get('fulfillment_service', 'manual'),
                'weight': float(first.get('weight', 0) or 0),
                'weight_unit': first.get('weight_unit', 'kg'),
            })
        
        # Images
        images = product.get('images', [])
        if images:
            main_image = images[0]
            try:
                img_data = requests.get(main_image.get('src', ''), timeout=10).content
                vals['image'] = base64.b64encode(img_data)
            except Exception as e:
                _logger.warning("Failed to download image: %s", str(e))
            
            image_vals = []
            for img in images:
                existing = self.env['sync.catalog.image'].search([
                    ('catalog_id', '=', self.id),
                    ('remote_id', '=', str(img.get('id', '')))
                ], limit=1)
                
                img_val = {
                    'catalog_id': self.id,
                    'remote_id': str(img.get('id', '')),
                    'src': img.get('src', ''),
                    'position': img.get('position', 0),
                    'alt': img.get('alt', ''),
                }
                if existing:
                    existing.write(img_val)
                else:
                    image_vals.append(img_val)
            
            if image_vals:
                self.env['sync.catalog.image'].create(image_vals)
        
        # Variants
        variant_vals = []
        for variant in variants:
            existing = self.env['sync.catalog.variant'].search([
                ('catalog_id', '=', self.id),
                ('remote_id', '=', str(variant.get('id', '')))
            ], limit=1)
            
            var_val = {
                'catalog_id': self.id,
                'remote_id': str(variant.get('id', '')),
                'name': variant.get('title', ''),
                'sku': variant.get('sku', ''),
                'price': float(variant.get('price', 0) or 0),
                'compare_at_price': float(variant.get('compare_at_price', 0) or 0),
                'inventory_quantity': variant.get('inventory_quantity', 0),
                'barcode': variant.get('barcode', ''),
                'weight': float(variant.get('weight', 0) or 0),
                'weight_unit': variant.get('weight_unit', 'kg'),
            }
            if existing:
                existing.write(var_val)
            else:
                variant_vals.append(var_val)
        
        if variant_vals:
            self.env['sync.catalog.variant'].create(variant_vals)
        
        self.write(vals)

    @api.model
    def sync_from_remote(self, store, since_date=None):
        """Bulk sync products from Shopify"""
        journal = self.env['sync.journal'].create({
            'name': 'Catalog Pull: %s' % store.name,
            'store_id': store.id,
            'direction': 'inbound',
            'target': 'catalog',
            'state': 'running',
            'total': 0,
            'passed': 0,
            'failed': 0,
        })
        
        try:
            params = {'limit': 250}
            if since_date:
                params['updated_at_min'] = since_date.isoformat()
            
            result = store._api_call('products.json', params=params)
            
            if not result.get('success'):
                journal.write({'state': 'failed', 'error': result.get('error', 'Unknown error')})
                return False
            
            products = result.get('data', {}).get('products', [])
            journal.write({'total': len(products)})
            
            for product in products:
                try:
                    existing = self.search([
                        ('remote_id', '=', str(product['id'])),
                        ('store_id', '=', store.id)
                    ], limit=1)
                    
                    if existing:
                        existing._update_from_shopify_data(product)
                        existing.write({'sync_state': 'synced'})
                    else:
                        new_product = self.create({
                            'name': product.get('title', 'Untitled'),
                            'remote_id': str(product['id']),
                            'store_id': store.id,
                            'sync_state': 'synced',
                        })
                        new_product._update_from_shopify_data(product)
                    
                    journal.passed += 1
                    
                except Exception as e:
                    journal.failed += 1
                    _logger.error("Catalog sync error: %s", str(e))
            
            journal.write({'state': 'done'})
            return True
            
        except Exception as e:
            journal.write({'state': 'failed', 'error': str(e)})
            return False


class SyncCatalogVariant(models.Model):
    _name = 'sync.catalog.variant'
    _description = 'Catalog Variant'
    _order = 'id'

    catalog_id = fields.Many2one('sync.catalog', string='Product', required=True, ondelete='cascade', index=True)
    remote_id = fields.Char(string='Remote Variant ID', index=True)
    name = fields.Char(string='Variant Title')
    sku = fields.Char(string='SKU', index=True)
    price = fields.Float(string='Price')
    compare_at_price = fields.Float(string='Compare at Price')
    inventory_quantity = fields.Integer(string='Inventory Qty', default=0)
    barcode = fields.Char(string='Barcode')
    weight = fields.Float(string='Weight')
    weight_unit = fields.Selection([
        ('kg', 'kg'),
        ('g', 'g'),
        ('lb', 'lb'),
        ('oz', 'oz')
    ], string='Weight Unit', default='kg')
    image = fields.Binary(string='Variant Image', attachment=True)
    sync_state = fields.Selection([
        ('pending', 'Pending'),
        ('synced', 'Synced'),
        ('error', 'Error'),
    ], default='pending')


class SyncCatalogImage(models.Model):
    _name = 'sync.catalog.image'
    _description = 'Catalog Image'
    _order = 'position, id'

    catalog_id = fields.Many2one('sync.catalog', string='Product', required=True, ondelete='cascade', index=True)
    remote_id = fields.Char(string='Remote Image ID', index=True)
    src = fields.Char(string='Image URL')
    position = fields.Integer(string='Position', default=0)
    alt = fields.Char(string='Alt Text')
    image = fields.Binary(string='Image Data', attachment=True)
