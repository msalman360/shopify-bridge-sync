from odoo import models, fields

class SyncJournal(models.Model):
    _name = 'sync.journal'
    _description = 'Synchronization Journal'
    
    name = fields.Char(string='Operation', required=True)
    store_id = fields.Many2one('sync.store', string='Store', required=True)
    direction = fields.Selection([
        ('inbound', 'Shopify → Odoo'),
        ('outbound', 'Odoo → Shopify'),
    ], string='Direction', default='inbound')
    target = fields.Selection([
        ('catalog', 'Product Catalog'),
        ('sale', 'Sales Orders'),
        ('contact', 'Contacts'),
    ], string='Target', default='catalog')
    state = fields.Selection([
        ('running', 'Running'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], default='running')
    total = fields.Integer(string='Total', default=0)
    passed = fields.Integer(string='Passed', default=0)
    failed = fields.Integer(string='Failed', default=0)
    error = fields.Text(string='Error Details')
