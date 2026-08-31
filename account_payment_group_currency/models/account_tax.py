from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from ast import literal_eval
from odoo.tools.safe_eval import safe_eval
from dateutil.relativedelta import relativedelta
import datetime


class AccountTax(models.Model):
    _inherit = "account.tax"

    def create_payment_withholdings(self, payment_group):
        res = super(AccountTax, self).create_payment_withholdings(payment_group)
        if payment_group.lines_same_currency_id and payment_group.lines_same_currency_id.id != payment_group.company_id.currency_id.id:
            for tax in self.filtered(lambda x: x.withholding_type != 'none'):
                payment_withholding = self.env[
                    'account.payment'].search([
                        ('payment_group_id', '=', payment_group.id),
                        ('tax_withholding_id', '=', tax.id),
                        ('automatic', '=', True),
                    ], limit=1)
                if payment_withholding:
                    group_currency = payment_group.lines_same_currency_id
                    # La cotizacion se pide por el UNICO punto de extension
                    # (_get_payment_exchange_rate) para que la retencion se
                    # value igual que el resto de las lineas del grupo: en los
                    # recibos de cliente, a tipo de cambio vendedor.
                    # Se consulta sobre un registro en memoria porque la
                    # retencion todavia esta en moneda de compania y el hook,
                    # preguntado sobre ella, devolveria 1.
                    probe = payment_withholding.new({
                        'payment_group_id': payment_group.id,
                        'company_id': payment_group.company_id.id,
                        'currency_id': group_currency.id,
                        'date': payment_group.payment_date,
                    })
                    rate = probe._get_payment_exchange_rate()
                    if not rate:
                        continue
                    payment_withholding.write({
                        'currency_id': group_currency.id,
                        'amount': group_currency.round(
                            payment_withholding.amount / rate),
                        'exchange_rate': rate,
                        # amount_company_currency NO se escribe: es derivado
                    })
        return res


