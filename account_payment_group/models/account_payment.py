# © 2016 ADHOC SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

import logging
_logger = logging.getLogger(__name__)


# Tupla que usa el guard de account.payment._synchronize_to_moves en Odoo 15
# (odoo/addons/account/models/account_payment.py:836-839).
# REVISAR en cada upgrade de core.
CORE_TRIGGER_FIELDS = (
    'date', 'amount', 'payment_type', 'partner_type', 'payment_reference',
    'is_internal_transfer', 'currency_id', 'partner_id',
    'destination_account_id', 'partner_bank_id', 'journal_id',
)


class AccountPayment(models.Model):
    _inherit = "account.payment"

    payment_group_id = fields.Many2one(
        'account.payment.group',
        'Payment Group',
        readonly=True,
    )
    amount_company_currency = fields.Monetary(
        string='Amount on Company Currency',
        compute='_compute_amount_company_currency',
        inverse='_inverse_amount_company_currency',
        currency_field='company_currency_id',
        help="En borrador se deriva de amount x cotizacion. Una vez "
             "contabilizado se lee del asiento: el mayor es la unica fuente de "
             "verdad, no se guarda una copia que pueda desviarse.",
    )
    other_currency = fields.Boolean(
        compute='_compute_other_currency',
    )
    # DEPRECADO - no usar en codigo nuevo. Columna conservada para no migrar los
    # 6.813 pagos historicos. Ya no participa del calculo ni del asiento.
    force_amount_company_currency = fields.Monetary(
        string='Forced Amount on Company Currency (DEPRECADO)',
        currency_field='company_currency_id',
        copy=False,
        readonly=True,
    )
    exchange_rate = fields.Float(
        string='Exchange Rate',
        # 10 decimales (antes 4): con 4, 32 de 3.189 pagos FX de produccion no
        # cierran al centavo. NO requiere migracion: fields.Float con digits
        # mapea a numeric sin precision declarada, el digits solo redondea en
        # Python.
        digits=(16, 10),
        copy=False,
    )
    l10n_ar_amount_company_currency_signed = fields.Monetary(
        currency_field='company_currency_id', compute='_compute_l10n_ar_amount_company_currency_signed')
    # campo a ser extendido y mostrar un nombre detemrinado en las lineas de
    # pago de un payment group o donde se desee (por ej. con cheque, retención,
    # etc)
    payment_method_description = fields.Char(
        compute='_compute_payment_method_description',
        string='Payment Method Desc.',
    )
    available_journal_ids = fields.Many2many(
        comodel_name='account.journal',
        compute='_compute_available_journal_ids'
    )

    label_journal_id = fields.Char(
        compute='_compute_label'
    )

    label_destination_journal_id = fields.Char(
        compute='_compute_label'
    )

    @api.depends('payment_type', 'payment_group_id')
    def _compute_available_journal_ids(self):
        """
        Este metodo odoo lo agrega en v16
        Igualmente nosotros lo modificamos acá para que funcione con esta logica:
        a) desde transferencias permitir elegir cualquier diario ya que no se selecciona compañía
        b) desde grupos de pagos solo permitir elegir diarios de la misma compañía
        NOTA: como ademas estamos mandando en el contexto del company_id, tal vez podriamos evitar pisar este metodo
        y ande bien en v16 para que las lineas de pago de un payment group usen la compañia correspondiente, pero
        lo que faltaria es hacer posible en las transferencias seleccionar una compañia distinta a la por defecto
        """
        journals = self.env['account.journal'].search([
            ('company_id', 'in', self.env.companies.ids), ('type', 'in', ('bank', 'cash'))
        ])
        for pay in self:
            filtered_domain = [('inbound_payment_method_line_ids', '!=', False)] if \
                pay.payment_type == 'inbound' else [('outbound_payment_method_line_ids', '!=', False)]
            if pay.payment_group_id:
                filtered_domain.append(('company_id', '=', pay.payment_group_id.company_id.id))
            pay.available_journal_ids = journals.filtered_domain(filtered_domain)

    @api.depends('payment_method_id')
    def _compute_payment_method_description(self):
        for rec in self:
            rec.payment_method_description = rec.payment_method_id.display_name

    @api.depends('amount_company_currency', 'payment_type')
    def _compute_l10n_ar_amount_company_currency_signed(self):
        """ new field similar to amount_company_currency_signed but:
        1. is positive for payments to suppliers
        2. we use the new field amount_company_currency instead of amount_total_signed, because amount_total_signed is
        computed only after saving
        We use l10n_ar prefix because this is a pseudo backport of future l10n_ar_withholding module """
        for payment in self:
            if payment.payment_type == 'outbound' and payment.partner_type == 'customer' or \
                    payment.payment_type == 'inbound' and payment.partner_type == 'supplier':
                payment.l10n_ar_amount_company_currency_signed = -payment.amount_company_currency
            else:
                payment.l10n_ar_amount_company_currency_signed = payment.amount_company_currency

    # ---------------- helpers ----------------

    def _seek_liquidity_lines(self):
        """ Version acotada de _seek_for_lines(): solo la ranura de liquidez.

        Dos diferencias con el core, ambas deliberadas:
        1. _get_valid_liquidity_accounts() se resuelve UNA vez (el core lo
           llama por cada linea del asiento).
        2. Se conserva el fallback del core: si el asiento no tiene ninguna
           linea en una cuenta de liquidez vigente -- tipico de pagos viejos
           cuyo diario cambio de cuenta transitoria -- se toma la unica linea
           que no es de deudas/creditos. Sin este fallback los historicos
           divergentes seguirian mostrando amount x cotizacion en vez del
           mayor, que es justamente lo que este rediseno corrige.
        """
        self.ensure_one()
        if not self.move_id:
            return self.env['account.move.line']
        valid_accounts = self._get_valid_liquidity_accounts()
        liquidity_lines = self.env['account.move.line']
        other_lines = self.env['account.move.line']
        for line in self.move_id.line_ids:
            if line.account_id in valid_accounts:
                liquidity_lines |= line
            elif line.account_id.internal_type not in ('receivable', 'payable') \
                    and line.account_id != line.company_id.transfer_account_id:
                other_lines |= line
        if not liquidity_lines and len(other_lines) == 1:
            return other_lines
        return liquidity_lines

    def _get_payment_exchange_rate(self):
        """ UNICO punto de extension para decidir que cotizacion aplica.
        Reemplaza los computes copiados en currency_rate_add_percent y
        rate_customize_payment.

        La cotizacion pactada en el grupo (lines_rate) la agrega
        account_payment_group_currency, que es el modulo que define ese campo:
        este modulo no puede depender de el ni nombrarlo en un @api.depends,
        porque se carga antes. """
        self.ensure_one()
        if not self.other_currency:
            return 1.0
        return self.currency_id._convert(
            1.0, self.company_currency_id, self.company_id,
            self.date or fields.Date.context_today(self), round=False)

    def _get_effective_exchange_rate(self):
        self.ensure_one()
        return self.exchange_rate or self._get_payment_exchange_rate()

    # ---------------- computes ----------------

    @api.depends('currency_id', 'company_currency_id')
    def _compute_other_currency(self):
        for rec in self:
            rec.other_currency = bool(
                rec.company_currency_id and rec.currency_id
                and rec.company_currency_id != rec.currency_id)

    @api.depends('amount', 'exchange_rate', 'other_currency',
                 'move_id.state',
                 'move_id.line_ids.debit', 'move_id.line_ids.credit',
                 'move_id.line_ids.account_id')
    def _compute_amount_company_currency(self):
        """ misma moneda -> amount | contabilizado -> el asiento |
            borrador -> amount x cotizacion """
        for rec in self:
            if not rec.other_currency:
                rec.amount_company_currency = rec.amount
                continue
            liquidity_lines = rec._seek_liquidity_lines()
            if rec.move_id.state != 'draft' and liquidity_lines:
                rec.amount_company_currency = abs(
                    sum(liquidity_lines.mapped('balance')))
            else:
                rec.amount_company_currency = rec.company_currency_id.round(
                    rec.amount * rec._get_effective_exchange_rate())

    @api.onchange('amount_company_currency')
    def _inverse_amount_company_currency(self):
        """ Editar el importe en moneda de compania AJUSTA LA COTIZACION.
        Ya no existe un importe forzado paralelo. Es inverse (write) y onchange
        (feedback en vivo). Idempotente: con 10 decimales el round-trip cierra. """
        for rec in self:
            if not rec.other_currency or not rec.amount:
                continue
            if rec.move_id.state != 'draft':
                continue          # un pago posteado no se re-cotiza por edicion
            rec.exchange_rate = rec.amount_company_currency / rec.amount

    def write(self, vals):
        """ Cuando la cotizacion y el importe en moneda de compania vienen en la
        misma escritura, manda la cotizacion.

        amount_company_currency es derivado y su inverse corre DESPUES de las
        escrituras directas: si el cliente manda el importe viejo junto con una
        cotizacion nueva -el caso del onchange del encabezado, que propone
        exchange_rate a la linea- el inverse recalcularia exchange_rate a partir
        del importe viejo y revertiria la cotizacion sin ningun error. """
        if vals.get('exchange_rate') and 'amount_company_currency' in vals:
            vals = {k: v for k, v in vals.items()
                    if k != 'amount_company_currency'}
        return super().write(vals)

    @api.onchange('payment_group_id')
    def onchange_payment_group_id(self):
        # now we change this according when use save & new the context from the payment was erased and we need to use some data.
        # this change is due this odoo change https://github.com/odoo/odoo/commit/c14b17c4855fd296fd804a45eab02b6d3566bb7a
        if self.payment_group_id:
            self.date = self.payment_group_id.payment_date
            self.partner_type = self.payment_group_id.partner_type
            self.partner_id = self.payment_group_id.partner_id
            self.payment_type = 'inbound' if self.payment_group_id.partner_type  == 'customer' else 'outbound'
            self.amount = self.payment_group_id.payment_difference

    @api.model_create_multi
    def create(self, vals_list):
        """ If a payment is created from anywhere else we create the payment group in top """
        recs = super().create(vals_list)
        for rec in recs.filtered(lambda x: not x.payment_group_id and not x.is_internal_transfer).with_context(
                created_automatically=True):
            if not rec.partner_id:
                raise ValidationError(_(
                    'Manual payments should not be created manually but created from Customer Receipts / Supplier Payments menus'))
            rec.payment_group_id = self.env['account.payment.group'].create({
                'company_id': rec.company_id.id,
                'partner_type': rec.partner_type,
                'partner_id': rec.partner_id.id,
                'payment_date': rec.date,
                'communication': rec.ref,
            })
            rec.payment_group_id.post()
        return recs

    @api.depends('payment_group_id')
    def _compute_destination_account_id(self):
        """
        If we are paying a payment gorup with paylines, we use account
        of lines that are going to be paid
        """
        for rec in self:
            to_pay_account = rec.payment_group_id.to_pay_move_line_ids.mapped(
                'account_id')
            if len(to_pay_account) > 1:
                raise ValidationError(_(
                    'To Pay Lines must be of the same account!'))
            elif len(to_pay_account) == 1:
                rec.destination_account_id = to_pay_account[0]
            else:
                super(AccountPayment, rec)._compute_destination_account_id()

    def show_details(self):
        """
        Metodo para mostrar form editable de payment, principalmente para ser
        usado cuando hacemos ajustes y el payment group esta confirmado pero
        queremos editar una linea
        """
        return {
            'name': _('Payment Lines'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'account.payment',
            'target': 'new',
            'res_id': self.id,
            'context': self._context,
        }

    def button_open_payment_group(self):
        self.ensure_one()
        return self.payment_group_id.get_formview_action()

    # ---------------- asiento ----------------

    def _prepare_move_line_default_vals(self, write_off_line_vals=None):
        """ El asiento se arma SIEMPRE con amount x exchange_rate.
        Tambien corrige el manejo de signos: antes se hacia
        'force - credit - debit' asumiendo cual de los dos era cero. """
        res = super()._prepare_move_line_default_vals(
            write_off_line_vals=write_off_line_vals)

        if not self.other_currency or not self.exchange_rate:
            return res

        liquidity_vals, counterpart_vals = res[0], res[1]
        liquidity_balance = self.company_currency_id.round(
            liquidity_vals['amount_currency'] * self.exchange_rate)
        difference = liquidity_balance - (
            liquidity_vals['debit'] - liquidity_vals['credit'])
        if self.company_currency_id.is_zero(difference):
            return res

        liquidity_vals.update({
            'debit': liquidity_balance if liquidity_balance > 0.0 else 0.0,
            'credit': -liquidity_balance if liquidity_balance < 0.0 else 0.0,
        })
        # El descuadre va integro a la contrapartida. Las lineas de write-off no
        # se tocan: ya estan contempladas en el balance que calculo el core.
        counterpart_balance = (
            counterpart_vals['debit'] - counterpart_vals['credit']) - difference
        counterpart_vals.update({
            'debit': counterpart_balance if counterpart_balance > 0.0 else 0.0,
            'credit': -counterpart_balance if counterpart_balance < 0.0 else 0.0,
        })
        return res

    # ---------------- sincronizacion ----------------

    @api.model
    def _get_trigger_fields_to_sincronize(self):
        """ Backport de v16. En v15 el core NO define este metodo, con lo cual
        los overrides de este modulo y de l10n_ar_ux eran codigo muerto. """
        return CORE_TRIGGER_FIELDS + ('exchange_rate',)

    def _synchronize_to_moves(self, changed_fields):
        """ Hace que el hook sea el registro efectivo de campos que disparan la
        resincronizacion. Si cambio uno de NUESTROS campos extra, agregamos
        'amount' para que el guard del core deje pasar.

        Solo para pagos cuyo asiento YA tiene lineas: al crear desde el form, el
        inverse de amount_company_currency escribe exchange_rate antes de que
        account.payment.create() arme line_ids. Si forzaramos la resincronizacion
        ahi, el core crearia la linea de liquidez y despues create() agregaria
        otra, y el asiento quedaria con dos (UserError "one and only one
        outstanding payments/receipts account"). """
        extra = set(self._get_trigger_fields_to_sincronize()) - set(CORE_TRIGGER_FIELDS)
        if not (extra & set(changed_fields)):
            return super()._synchronize_to_moves(changed_fields)
        with_lines = self.filtered(lambda pay: pay.move_id.line_ids)
        res = super(AccountPayment, with_lines)._synchronize_to_moves(
            set(changed_fields) | {'amount'})
        super(AccountPayment, self - with_lines)._synchronize_to_moves(changed_fields)
        return res

    @api.depends_context('default_is_internal_transfer')
    def _compute_is_internal_transfer(self):
        """ Este campo se recomputa cada vez que cambia un diario y queda en False porque el segundo diario no va a
        estar completado. Como nosotros tenemos un menú especifico para poder registrar las transferencias internas,
        entonces si estamos en este menu siempre es transferencia interna"""
        if self._context.get('default_is_internal_transfer'):
            self.is_internal_transfer = True
        else:
            return super()._compute_is_internal_transfer()

    def _create_paired_internal_transfer_payment(self):
        for rec in self:
            super(AccountPayment, rec.with_context(
                default_exchange_rate=rec.exchange_rate
            ))._create_paired_internal_transfer_payment()

    @api.onchange("payment_type")
    def _compute_label(self):
        for rec in self:
            if rec.payment_type == "outbound":
                rec.label_journal_id = "Diario de origen"
                rec.label_destination_journal_id = "Diario de destino"
            else:
                rec.label_journal_id = "Diario de destino"
                rec.label_destination_journal_id = "Diario de origen"

    # ---------------- red de seguridad ----------------

    @api.constrains('amount', 'exchange_rate', 'move_id')
    def _check_move_matches_payment(self):
        """ Invariante que el modulo violo 232 veces sin que nadie se enterara. """
        for rec in self:
            if not rec.other_currency or rec.move_id.state == 'draft':
                continue
            liquidity_lines = rec._seek_liquidity_lines()
            if not liquidity_lines:
                continue
            asiento = abs(sum(liquidity_lines.mapped('balance')))
            esperado = rec.company_currency_id.round(
                rec.amount * rec._get_effective_exchange_rate())
            if abs(asiento - esperado) > 1.0:      # tolerancia por redondeos
                raise ValidationError(_(
                    "El asiento del pago %s tiene %s en moneda de compania pero "
                    "el importe %s a cotizacion %s da %s."
                ) % (rec.display_name, asiento, rec.amount,
                     rec._get_effective_exchange_rate(), esperado))
