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
        inverse='_inverse_lines_same_currency_id',
        store=True
    )

    lines_rate = fields.Monetary(
        string="Currency rate",
        tracking=True,
        help="Change rate to use"
    )

    def write(self, vals):
        res = super(AccountPaymentGroup,self).write(vals)
        if 'lines_rate' in vals:
            for payment in self.payment_ids:
                payment.write({'exchange_rate':vals['lines_rate']})
                payment.write({'amount_company_currency':payment.amount*vals['lines_rate']})
                payment.move_id.write({'l10n_ar_currency_rate':vals['lines_rate']})
                payment.move_id._compute_amount()
        return res

    def _inverse_lines_same_currency_id(self):
        return True

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

    payment_difference_currency = fields.Monetary(
        compute='_compute_payment_difference_currency',
        readonly=True,
        currency_field='lines_same_currency_id',
    )

    @api.depends('selected_debt_currency','to_pay_amount_currency','payments_amount_currency')
    def _compute_payment_difference_currency(self):
        for rec in self:
            rec.payment_difference_currency = rec.to_pay_amount_currency - rec.payments_amount_currency

    payments_amount_currency = fields.Monetary(
        compute='_compute_payments_amount_currency',
        currency_field='lines_same_currency_id',
        string='Total Pagos',
        tracking=True,
    )   

    @api.depends('payment_ids.amount_total_signed')
    def _compute_payments_amount_currency(self):
        for rec in self:
            rec.payments_amount_currency = sum((rec._origin.payment_ids + rec.payment_ids.filtered(lambda x: not x.ids)).mapped(
                'amount'))

    to_pay_amount_currency = fields.Monetary(
        compute='_compute_to_pay_amount_currency',
        inverse='_inverse_to_pay_amount_currency',
        string='Importe a pagar',
        readonly=True,
        states={'draft': [('readonly', False)]},
        tracking=True,
    )
    unreconciled_amount_currency = fields.Monetary(
        string='Adjustment / Advance Currency',
        readonly=True,
        states={'draft': [('readonly', False)]},
    )    

    @api.depends(
        'selected_debt_currency', 'unreconciled_amount_currency')
    def _compute_to_pay_amount_currency(self):
        for rec in self:
            rec.to_pay_amount_currency = rec.selected_debt_currency + rec.unreconciled_amount_currency    

    @api.onchange('to_pay_amount_currency')
    def _inverse_to_pay_amount_currency(self):
        for rec in self:
            rec.unreconciled_amount_currency = rec.to_pay_amount_currency - rec.selected_debt_currency

    @api.onchange('payments_amount_currency')
    def _inverse_payments_amount_currency(self):
        for rec in self:
            rec.to_pay_amount = rec.payments_amount_currency * rec.lines_rate

    matched_amount_currency = fields.Monetary(
        compute='_compute_matched_amounts_currency',
        currency_field='lines_same_currency_id',
    )
    unmatched_amount_currency = fields.Monetary(
        compute='_compute_matched_amounts_currency',
        currency_field='lines_same_currency_id',
    )    

    @api.depends(
        'state',
        'payments_amount_currency',
        )
    def _compute_matched_amounts_currency(self):
        for rec in self:
            rec.matched_amount_currency = 0.0
            rec.unmatched_amount_currency = 0.0
            if rec.state != 'posted':
                continue
            sign = rec.partner_type == 'supplier' and -1.0 or 1.0
            rec.matched_amount_currency = sign * sum(
                rec.matched_move_line_ids.with_context(payment_group_id=rec.id).mapped('payment_group_matched_amount_currency'))
            rec.unmatched_amount_currency = rec.payments_amount_currency - rec.matched_amount_currency


    selected_debt_currency = fields.Monetary(
        string='Total Seleccionado',
        currency_field='lines_same_currency_id',        
        compute='_compute_selected_debt_currency',
    )    

    @api.depends('to_pay_move_line_ids.amount_residual_currency')
    def _compute_selected_debt_currency(self):
        for rec in self:
            rec.selected_debt_currency = sum(rec.to_pay_move_line_ids._origin.mapped('amount_residual_currency')) * (-1.0 if rec.partner_type == 'supplier' else 1.0)


    amount_balance_currency = fields.Monetary(
        compute='_compute_amount_balance_currency',
        string='Nuevo Saldo',
        currency_field='lines_same_currency_id',        
        readonly=True,
        tracking=True,
    )

    amount_balance = fields.Monetary(
        compute='_compute_amount_balance',
        string='Nuevo Saldo',
        readonly=True,
        tracking=True,
    )

    @api.depends('selected_debt_currency','to_pay_amount_currency','payments_amount_currency')
    def _compute_amount_balance_currency(self):
        for rec in self:
            rec.amount_balance_currency = rec.selected_debt_currency - rec.to_pay_amount_currency

    @api.depends('selected_debt','to_pay_amount','payments_amount')
    def _compute_amount_balance(self):
        for rec in self:
            rec.amount_balance = rec.selected_debt - rec.to_pay_amount            