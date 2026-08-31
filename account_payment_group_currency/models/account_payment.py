from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

import logging

_logger = logging.getLogger(__name__)


class AccountPayment(models.Model):
    _inherit = "account.payment"

    def _group_rate_applies(self):
        """ La cotizacion pactada en el encabezado vale SOLO para las lineas en
        la moneda del encabezado: un grupo puede tener pagos en otras monedas
        (una deuda en USD cancelada en parte con una transferencia en CNY) y
        esos se cotizan por su cuenta. """
        self.ensure_one()
        group = self.payment_group_id
        return bool(
            self.other_currency and group.lines_rate
            and self.currency_id == group.lines_same_currency_id)

    def _get_payment_exchange_rate(self):
        """ La cotizacion pactada en el grupo manda sobre la del dia.
        Vive aca y no en account_payment_group porque lines_rate lo define este
        modulo. """
        self.ensure_one()
        if self._group_rate_applies():
            return self.payment_group_id.lines_rate
        return super()._get_payment_exchange_rate()

    # OJO: @api.depends NO se acumula entre overrides, el del metodo mas
    # derivado reemplaza al del base. Por eso se repite la lista completa de
    # account_payment_group y se le suman las tres claves de este modulo. Si
    # cambia alla, cambiar aca.
    @api.depends('amount', 'exchange_rate', 'other_currency',
                 'currency_id',
                 'payment_group_id.lines_rate',
                 'payment_group_id.lines_same_currency_id',
                 'move_id.state',
                 'move_id.line_ids.debit', 'move_id.line_ids.credit',
                 'move_id.line_ids.account_id')
    def _compute_amount_company_currency(self):
        """ Solo agrega al @api.depends del metodo base las claves que decide
        este modulo: sin esto la pantalla no se refrescaria al cambiar la
        cotizacion del grupo, ni al cambiar la moneda de la linea o la del
        encabezado, de cuya comparacion depende que la cotizacion pactada
        aplique (_group_rate_applies). El modulo base no puede declarar esas
        dependencias porque se carga antes que este y los campos todavia no
        existen. """
        return super()._compute_amount_company_currency()

    @api.onchange('payment_group_id')
    def onchange_payment_group_id(self):
        """ Si el grupo opera en moneda extranjera, la linea SIEMPRE se expresa
        en esa moneda y toma el saldo pendiente en esa moneda. La cotizacion se
        propone una vez y el importe en moneda de compania se DERIVA. """
        res = super().onchange_payment_group_id()
        group = self.payment_group_id
        if not group:
            return res
        group_currency = group.lines_same_currency_id
        if not group_currency or group_currency == group.company_id.currency_id:
            return res
        self.currency_id = group_currency
        self.amount = group.payment_difference_currency
        # La cotizacion se pide siempre por el hook: la linea ya quedo en la
        # moneda del encabezado, asi que el devuelve lines_rate cuando aplica.
        self.exchange_rate = self._get_payment_exchange_rate()
        # NO se asigna amount_company_currency: es derivado.
        return res
