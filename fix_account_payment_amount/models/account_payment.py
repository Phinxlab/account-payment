
# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models, tools, _


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    @api.onchange('amount_company_currency')
    def _inverse_amount_company_currency(self):
        for rec in self:
            if rec.payment_group_id:
                if rec.payment_group_id.receiptbook_id:
                    if rec.payment_group_id.receiptbook_id.partner_type != 'customer':
                        if rec.other_currency and rec.amount_company_currency != \
                                rec.currency_id._convert(
                                    rec.amount, rec.company_id.currency_id,
                                    rec.company_id, rec.date):
                        # if rec.force_amount_company_currency:
                            rec.amount =  rec.amount_company_currency / rec.exchange_rate
        res = super(AccountPayment, self)._inverse_amount_company_currency()
        for rec in self:
            if rec.payment_group_id:
                if rec.payment_group_id.receiptbook_id:
                    if rec.payment_group_id.receiptbook_id.partner_type != 'customer':
                        rec.force_amount_company_currency =  rec.amount_company_currency
        return res
