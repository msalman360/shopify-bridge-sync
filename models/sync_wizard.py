from odoo import models, fields

class SyncWizard(models.TransientModel):
    _name = 'sync.wizard'
    _description = 'Manual Sync Wizard'
    
    store_id = fields.Many2one('sync.store', string='Store', required=True)
    scope = fields.Selection([
        ('catalog', 'Products'),
        ('sale', 'Orders'),
        ('contact', 'Customers'),
    ], default='catalog')
