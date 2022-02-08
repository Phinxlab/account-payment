from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from ast import literal_eval
from odoo.tools.safe_eval import safe_eval
from dateutil.relativedelta import relativedelta
import datetime


class AccountTax(models.Model):
    _inherit = "account.tax"

    def create_payment_withholdings(self, payment_group):
        super(AccountTax, self).create_payment_withholdings(payment_group)
        if payment_group.lines_same_currency_id and payment_group.lines_same_currency_id.id != payment_group.company_id.currency_id.id:
            for tax in self.filtered(lambda x: x.withholding_type != 'none'):
                payment_withholding = self.env[
                    'account.payment'].search([
                        ('payment_group_id', '=', payment_group.id),
                        ('tax_withholding_id', '=', tax.id),
                        ('automatic', '=', True),
                    ], limit=1)
                if payment_withholding:
                    vals2update = {'currency_id': payment_group.lines_same_currency_id}
                    if payment_group.lines_rate:
                        vals2update['amount'] = payment_withholding.amount / payment_group.lines_rate
                    else:
                        vals2update['amount'] = payment_group.company_id.currency_id._convert(
                            payment_withholding.amount,
                            payment_group.lines_same_currency_id,
                            payment_group.company_id,
                            payment_group.payment_date)
                    vals2update['amount_company_currency'] = payment_withholding.amount
                    payment_withholding.write(vals2update)
        return True


