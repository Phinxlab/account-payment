##############################################################################
# For copyright and license notices, see __manifest__.py file in module root
# directory
##############################################################################
from odoo import models, api, fields, _
from odoo.exceptions import ValidationError


class AccountPaymentGroup(models.Model):

    _inherit = "account.payment.group"

    withholdings_amount = fields.Monetary(
        compute='_compute_withholdings_amount'
    )
    withholdable_advanced_amount = fields.Monetary(
        'Adjustment / Advance (untaxed)',
        help='Sometimes used for withholdings calculation',
        readonly=True,
        states={'draft': [('readonly', False)]},
    )
    selected_debt_untaxed = fields.Monetary(
        # string='To Pay lines Amount',
        string='Selected Debt Untaxed',
        compute='_compute_selected_debt_untaxed',
    )
    matched_amount_untaxed = fields.Monetary(
        compute='_compute_matched_amount_untaxed',
        currency_field='currency_id',
    )
    
    def get_amount_currency(self):
        for rec in self:
            amount_currency_usd = 0
            currency_usd = self.env.ref('base.USD')
            currency_ars = self.env.ref('base.ARS')
            currency_id = rec.lines_same_currency_id or rec.company_id.currency_id
            if self._context.get('payment_acumulate'):
                amount_currency = rec.matched_amount_untaxed 
                if currency_id != rec.company_id.currency_id:
                    return (rec._get_matched_amount_untaxed_currency() * rec.lines_rate)
                else:
                    return amount_currency
            else:
                amount_currency = rec._get_mached_amount_untaxed(rec.to_pay_amount_currency)
                if currency_id != currency_ars:
                    amount_currency_usd = currency_id._convert(amount_currency, currency_usd, rec.company_id, rec.payment_date)
                else:
                    return amount_currency
            return amount_currency_usd

    def _compute_matched_amount_untaxed(self):
        """ Lo separamos en otro metodo ya que es un poco mas costoso y no se
        usa en conjunto con matched_amount
        """
        for rec in self:
            rec.matched_amount_untaxed = 0.0
            if rec.state != 'posted':
                continue
            matched_amount_untaxed = 0.0
            sign = rec.partner_type == 'supplier' and -1.0 or 1.0
            for line in rec.matched_move_line_ids.with_context(
                    payment_group_id=rec.id):
                invoice = line.move_id
                factor = invoice and invoice._get_tax_factor() or 1.0
                matched_amount_untaxed += \
                    line.payment_group_matched_amount * factor
            rec.matched_amount_untaxed = sign * matched_amount_untaxed

    def _get_matched_amount_untaxed_currency(self):
        for rec in self:
            matched_amount_untaxed_currency = 0.0
            if rec.state != 'posted':
                continue
            sign = rec.partner_type == 'supplier' and -1.0 or 1.0
            for line in rec.matched_move_line_ids.with_context(
                    payment_group_id=rec.id):
                invoice = line.move_id
                factor = invoice and invoice._get_tax_factor() or 1.0
                matched_amount_untaxed_currency += \
                    line.payment_group_matched_amount_currency * factor
            return sign * matched_amount_untaxed_currency

    def _get_mached_amount_untaxed(self, to_pay_amount_currency):
        amount_untaxed = 0.0
        amount_tax = 0.0
        amount = 0.0
        sign = self.partner_type == 'supplier' and -1.0 or 1.0
        for line in self.to_pay_move_line_ids:
            invoice = line.move_id
            factor = invoice and invoice._get_tax_factor() or 1.0
            amount_untaxed += \
                line.price_total * factor
            amount_tax += \
                line.price_total
        amount_untaxed = amount_untaxed
        amount_tax = amount_tax
        if amount_tax != 0:
            amount = to_pay_amount_currency * amount_untaxed / amount_tax
        else:
            amount = to_pay_amount_currency
        return amount

    @api.depends(
        'to_pay_move_line_ids.amount_residual',
        'to_pay_move_line_ids.amount_residual_currency',
        'to_pay_move_line_ids.currency_id',
        'to_pay_move_line_ids.move_id',
        'payment_date',
        'currency_id',
    )
    def _compute_selected_debt_untaxed(self):
        for rec in self:
            selected_debt_untaxed = 0.0
            for line in rec.to_pay_move_line_ids._origin:
                # factor for total_untaxed
                invoice = line.move_id
                factor = invoice and invoice._get_tax_factor() or 1.0
                if self.lines_same_currency_id == self.company_id.currency_id:
                    selected_debt_untaxed += line.amount_residual * factor
                else:
                    selected_debt_untaxed += line.amount_residual_currency * factor * rec.lines_rate
            
            if abs(selected_debt_untaxed) > abs(rec.to_pay_amount_currency * rec.lines_rate):
                selected_debt_untaxed = rec.to_pay_amount_currency * rec.lines_rate * -1
            
            rec.selected_debt_untaxed = selected_debt_untaxed * (rec.partner_type == 'supplier' and -1.0 or 1.0)

    @api.onchange('unreconciled_amount')
    def set_withholdable_advanced_amount(self):
        for rec in self:
            rec.withholdable_advanced_amount = rec.unreconciled_amount

    @api.depends(
        'payment_ids.tax_withholding_id',
        'payment_ids.amount',
    )
    def _compute_withholdings_amount(self):
        for rec in self:
            rec.withholdings_amount = sum(
                rec.payment_ids.filtered(lambda x: x.tax_withholding_id).mapped('amount'))

    def compute_withholdings(self):
        for rec in self:
            if rec.partner_type != 'supplier':
                continue
            # limpiamos el type por si se paga desde factura ya que el en ese
            # caso viene in_invoice o out_invoice y en search de tax filtrar
            # por impuestos de venta y compra (y no los nuestros de pagos
            # y cobros)
            rec.payment_date = fields.Date.today()
            withholding_lines = rec.payment_ids.filtered(lambda x: x.tax_withholding_id)
            withholding_lines.unlink()
            self.env['account.tax'].with_context(type=None).search([
                ('type_tax_use', '=', rec.partner_type),
                ('company_id', '=', rec.company_id.id),
            ]).create_payment_withholdings(rec)

    def confirm(self):
        res = super(AccountPaymentGroup, self).confirm()
        for rec in self:
            if rec.company_id.automatic_withholdings:
                rec.compute_withholdings()
        return res

    def _get_withholdable_amounts(
            self, withholding_amount_type, withholding_advances):
        """ Method to help on getting withholding amounts from account.tax
        """
        self.ensure_one()
        # Por compatibilidad con public_budget aceptamos
        # pagos en otros estados no validados donde el matched y
        # unmatched no se computaron, por eso agragamos la condición
        if self.state == 'posted':
            untaxed_field = 'matched_amount_untaxed'
            total_field = 'matched_amount'
        else:
            untaxed_field = 'selected_debt_untaxed'
            total_field = 'selected_debt'

        if withholding_amount_type == 'untaxed_amount':
            withholdable_invoiced_amount = self[untaxed_field]
        else:
            withholdable_invoiced_amount = self[total_field]

        if self._context.get('currency_id'):
            withholdable_invoiced_amount = self.get_amount_currency()

        withholdable_advanced_amount = 0.0
        # if the unreconciled_amount is negative, then the user wants to make
        # a partial payment. To get the right untaxed amount we need to know
        # which invoice is going to be paid, we only allow partial payment
        # on last invoice.
        # If the payment is posted the withholdable_invoiced_amount is
        # the matched amount
        if self.withholdable_advanced_amount < 0.0 and \
                self.to_pay_move_line_ids and self.state != 'posted':
            withholdable_advanced_amount = 0.0

            sign = self.partner_type == 'supplier' and -1.0 or 1.0
            sorted_to_pay_lines = sorted(
                self.to_pay_move_line_ids,
                key=lambda a: a.date_maturity or a.date)

            # last line to be reconciled
            partial_line = sorted_to_pay_lines[-1]
            if sign * partial_line.amount_residual < \
                    sign * self.withholdable_advanced_amount:
                raise ValidationError(_(
                    'Seleccionó deuda por %s pero aparentente desea pagar '
                    ' %s. En la deuda seleccionada hay algunos comprobantes de'
                    ' mas que no van a poder ser pagados (%s). Deberá quitar '
                    ' dichos comprobantes de la deuda seleccionada para poder '
                    'hacer el correcto cálculo de las retenciones.' % (
                        self.selected_debt,
                        self.to_pay_amount,
                        partial_line.move_id.display_name,
                        )))

            if withholding_amount_type == 'untaxed_amount' and \
                    partial_line.move_id:
                invoice_factor = partial_line.move_id._get_tax_factor()
            else:
                invoice_factor = 1.0

            # si el adelanto es negativo estamos pagando parcialmente una
            # factura y ocultamos el campo sin impuesto ya que lo sacamos por
            # el proporcional descontando de el iva a lo que se esta pagando
            withholdable_invoiced_amount -= (
                sign * self.unreconciled_amount * invoice_factor)
        elif withholding_advances:
            # si el pago esta publicado obtenemos los valores de los importes
            # conciliados (porque el pago pudo prepararse como adelanto
            # pero luego haberse conciliado y en ese caso lo estariamos sumando
            # dos veces si lo usamos como base de otros pagos). Si estan los
            # campos withholdable_advanced_amount y unreconciled_amount le
            # sacamos el proporcional correspondiente
            if self.state == 'posted':
                if self.unreconciled_amount and \
                   self.withholdable_advanced_amount:
                    withholdable_advanced_amount = self.unmatched_amount * (
                        self.withholdable_advanced_amount /
                        self.unreconciled_amount)
                else:
                    withholdable_advanced_amount = self.unmatched_amount
            else:
                withholdable_advanced_amount = \
                    self.withholdable_advanced_amount
        return (withholdable_advanced_amount, withholdable_invoiced_amount)
    
    # def post(self):
    #     for rec in self:
    #         rec.payment_date = fields.Date.today()
    #         withholding_lines = rec.payment_ids.filtered(lambda x: x.tax_withholding_id)
    #         if withholding_lines:
    #             withholding_lines.unlink()
    #             rec.compute_withholdings()
    #             rec.env.cr.commit()
    #     return super(AccountPaymentGroup, self).post()