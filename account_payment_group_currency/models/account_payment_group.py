# © 2016 ADHOC SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, api, fields, _
from odoo.exceptions import ValidationError


class AccountPaymentGroup(models.Model):
    _name = "account.payment.group"
    _inherit = "account.payment.group"

    lines_same_currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        compute='_compute_lines_same_currency_id',
        store=True
    )

    lines_rate = fields.Monetary(
        string="Currency rate",
        help="Change rate to use"
    )

    @api.onchange('lines_same_currency_id')
    def onchange_lines_same_currency_id(self):
        if self.lines_same_currency_id and self.lines_rate == float(0):
            self.lines_rate = self.env['res.currency']._get_conversion_rate(self.lines_same_currency_id,
                                                                            self.company_id.currency_id,
                                                                            self.company_id,
                                                                            self.payment_date or fields.Date.today())
        elif self.lines_same_currency_id is False:
            self.lines_rate = float(0)


    @api.depends('to_pay_move_line_ids.currency_id')
    def _compute_lines_same_currency_id(self):
        for rec in self:
            lines_currencies = rec.to_pay_move_line_ids.mapped('currency_id')
            if len(lines_currencies) == 1 and lines_currencies[0].id != rec.company_id.currency_id.id:
                rec.lines_same_currency_id = lines_currencies[0]
            else:
                rec.lines_same_currency_id = False

