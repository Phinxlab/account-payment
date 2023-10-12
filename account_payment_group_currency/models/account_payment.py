from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

import logging

_logger = logging.getLogger(__name__)


class AccountPayment(models.Model):
    _inherit = "account.payment"

    @api.onchange('payment_group_id')
    def onchange_payment_group_id(self):
        payment_difference_currency = self.payment_group_id.payment_difference_currency
        super(AccountPayment, self).onchange_payment_group_id()
        if self.payment_group_id and self.payment_group_id.lines_same_currency_id and self.payment_group_id.lines_same_currency_id.id != self.payment_group_id.company_id.currency_id.id:
            self.currency_id = self.payment_group_id.lines_same_currency_id
            _amount_company_currency = self.amount
            if self.payment_group_id.lines_rate:
                _amount_company_currency = payment_difference_currency * self.payment_group_id.lines_rate
                self.amount = payment_difference_currency
                self.exchange_rate = self.payment_group_id.lines_rate
            else:
                self.amount = self.payment_group_id.company_id.currency_id._convert(self.amount,
                                                                                    self.payment_group_id.lines_same_currency_id,
                                                                                    self.payment_group_id.company_id,
                                                                                    self.date)
            self.amount_company_currency = _amount_company_currency
