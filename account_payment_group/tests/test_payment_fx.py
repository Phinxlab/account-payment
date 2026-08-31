# © 2016 ADHOC SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""
Tests del rediseño del cálculo de importes en moneda extranjera.

Invariante que se protege: `amount_company_currency` y el balance de la línea de
liquidez del asiento NUNCA divergen. En borrador la verdad es
`(amount, exchange_rate)`; una vez contabilizado la verdad es el asiento.

No se usa AccountTestInvoicingCommon a propósito: su `setup_armageddon_tax`
choca con la validación de country del grupo de impuestos de la localización
argentina. El fixture se arma sobre la compañía y el diario que ya existen en la
base, y todo queda dentro del savepoint del test.
"""
from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPaymentFX(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.company = cls.env['res.company'].search(
            [('currency_id.name', '=', 'ARS')], limit=1)
        if not cls.company:
            cls.company = cls.env.company
        cls.company_currency = cls.company.currency_id

        cls.bank_journal = cls.method_line = None
        for journal in cls.env['account.journal'].search(
                [('type', '=', 'bank'), ('company_id', '=', cls.company.id)]):
            for line in journal._get_available_payment_method_lines('outbound'):
                if line.payment_account_id or \
                        cls.company.account_journal_payment_credit_account_id:
                    cls.bank_journal, cls.method_line = journal, line
                    break
            if cls.bank_journal:
                break

        cls.rate_value = 1462.25
        cls.foreign_currency = cls.env['res.currency'].create({
            'name': 'FXT',
            'symbol': 'FXT',
            # mismo redondeo que el USD y el CNY de esta base: sin esto los
            # importes de los casos testigo (1477,2515) se truncan a 2 decimales
            # y los tests dejan de medir lo que dicen medir.
            'rounding': 0.000001,
        })
        cls.env['res.currency.rate'].create({
            'name': fields.Date.from_string('2020-01-01'),
            'currency_id': cls.foreign_currency.id,
            'company_id': cls.company.id,
            'rate': 1.0 / cls.rate_value,
        })
        # Segunda moneda: un grupo puede tener lineas en mas de una moneda y la
        # cotizacion pactada en el encabezado cubre solo las de su moneda.
        cls.rate_value_alt = 980.5
        cls.foreign_currency_alt = cls.env['res.currency'].create({
            'name': 'FXU',
            'symbol': 'FXU',
            'rounding': 0.000001,
        })
        cls.env['res.currency.rate'].create({
            'name': fields.Date.from_string('2020-01-01'),
            'currency_id': cls.foreign_currency_alt.id,
            'company_id': cls.company.id,
            'rate': 1.0 / cls.rate_value_alt,
        })
        cls.partner = cls.env['res.partner'].create({'name': 'FX Test Partner'})
        cls.payment_date = fields.Date.from_string('2026-08-20')

    def setUp(self):
        super().setUp()
        if not self.bank_journal:
            self.skipTest(
                'No hay un diario bancario con cuenta transitoria configurada')

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _new_group(self, **kwargs):
        vals = {
            'company_id': self.company.id,
            'partner_type': 'supplier',
            'partner_id': self.partner.id,
            'payment_date': self.payment_date,
        }
        vals.update(kwargs)
        return self.env['account.payment.group'].create(vals)

    def _new_payment(self, amount, exchange_rate=None, currency=None, group=None,
                     post=False):
        """ Crea una línea de pago saliente sin pasar por el form: los tests de
        orden usan Form() aparte. """
        group = group if group is not None else self._new_group()
        payment = self.env['account.payment'].create({
            'payment_group_id': group.id,
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': self.partner.id,
            'journal_id': self.bank_journal.id,
            'company_id': self.company.id,
            'payment_method_line_id': self.method_line.id,
            'date': self.payment_date,
            'amount': amount,
            'currency_id': (currency or self.foreign_currency).id,
        })
        if exchange_rate is not None:
            payment.exchange_rate = exchange_rate
        if post:
            payment.action_post()
        return payment

    def _payment_form(self, group=None):
        group = group if group is not None else self._new_group()
        form = Form(self.env['account.payment'].with_context(
            default_payment_group_id=group.id,
            default_payment_type='outbound',
            default_partner_type='supplier',
        ))
        form.partner_id = self.partner
        form.journal_id = self.bank_journal
        form.payment_method_line_id = self.method_line
        form.date = self.payment_date
        form.currency_id = self.foreign_currency
        return form

    def _liquidity_balance(self, payment):
        return abs(sum(payment._seek_liquidity_lines().mapped('balance')))

    def _has_group_currency_module(self):
        return 'lines_rate' in self.env['account.payment.group']._fields

    # ------------------------------------------------------------------
    # invariante central
    # ------------------------------------------------------------------

    def test_asiento_siempre_igual_a_pantalla(self):
        payment = self._new_payment(3398.86, exchange_rate=1520.0, post=True)
        self.assertEqual(payment.state, 'posted')
        self.assertAlmostEqual(payment.amount_company_currency, 5166267.20, 2)
        self.assertAlmostEqual(
            self._liquidity_balance(payment), payment.amount_company_currency, 2)

    # ------------------------------------------------------------------
    # independencia del orden (el síntoma reportado por el usuario)
    # ------------------------------------------------------------------

    def test_orden_importe_despues_cotizacion(self):
        form = self._payment_form()
        form.amount = 3398.86
        form.exchange_rate = 1520.0
        self.assertAlmostEqual(form.amount_company_currency, 5166267.20, 2)
        payment = form.save()
        self.assertAlmostEqual(payment.amount_company_currency, 5166267.20, 2)

    def test_orden_cotizacion_despues_importe(self):
        form = self._payment_form()
        form.exchange_rate = 1520.0
        form.amount = 3398.86
        self.assertAlmostEqual(form.amount_company_currency, 5166267.20, 2)
        payment = form.save()
        self.assertAlmostEqual(payment.amount_company_currency, 5166267.20, 2)

    def test_orden_cotizacion_importe_cotizacion(self):
        form = self._payment_form()
        form.exchange_rate = 1400.0
        form.amount = 3398.86
        form.exchange_rate = 1520.0
        self.assertAlmostEqual(form.amount_company_currency, 5166267.20, 2)
        payment = form.save()
        self.assertAlmostEqual(payment.amount_company_currency, 5166267.20, 2)

    def test_alta_desde_el_form_no_duplica_la_linea_de_liquidez(self):
        """ Regresión: el inverse de amount_company_currency escribe
        exchange_rate durante create(), antes de que el core arme line_ids.
        Resincronizar ahí dejaba el asiento con dos líneas de liquidez. """
        form = self._payment_form()
        form.amount = 3398.86
        form.exchange_rate = 1520.0
        payment = form.save()
        self.assertEqual(len(payment._seek_liquidity_lines()), 1)
        self.assertEqual(len(payment.move_id.line_ids), 2)

    def test_cambiar_importe_arrastra_el_ars(self):
        """ Regresión PG 553077: el importe en moneda de compañía quedaba
        congelado del `amount` anterior. """
        payment = self._new_payment(486.66, exchange_rate=self.rate_value)
        self.assertAlmostEqual(
            payment.amount_company_currency,
            self.company_currency.round(486.66 * self.rate_value), 2)
        payment.amount = 1477.2515
        self.assertAlmostEqual(
            payment.amount_company_currency,
            self.company_currency.round(1477.2515 * self.rate_value), 2)
        self.assertNotAlmostEqual(payment.amount_company_currency, 711625.00, 2)

    # ------------------------------------------------------------------
    # round-trip (el workaround manual del usuario)
    # ------------------------------------------------------------------

    def test_editar_ars_ajusta_cotizacion_y_vuelve_al_centavo(self):
        payment = self._new_payment(486.414, exchange_rate=self.rate_value)
        payment.amount_company_currency = 711260.25
        self.assertAlmostEqual(payment.amount_company_currency, 711260.25, 2)
        payment.action_post()
        self.assertAlmostEqual(self._liquidity_balance(payment), 711260.25, 2)

    def test_precision_cotizacion_diez_decimales(self):
        """ Pares (importe, importe en moneda de compañía) que NO cierran al
        centavo con digits=(16,4) y sí con digits=(16,10). """
        pares = [
            (486.664, 711625.00),
            (9073.155, 13267220.75),
            (13786.693, 20159592.00),
            (19013.724, 27802817.77),
        ]
        for amount, company_amount in pares:
            with self.subTest(amount=amount):
                payment = self._new_payment(amount)
                payment.amount_company_currency = company_amount
                self.assertAlmostEqual(
                    payment.amount_company_currency, company_amount, 2,
                    "El round-trip no cierra al centavo para %s" % amount)

    # ------------------------------------------------------------------
    # el grupo no reescribe pagos ya grabados (D2)
    # ------------------------------------------------------------------

    def test_cambiar_lines_rate_no_pisa_pago_posteado(self):
        if not self._has_group_currency_module():
            self.skipTest('account_payment_group_currency no está instalado')
        group = self._new_group()
        payment = self._new_payment(
            3398.86, exchange_rate=1520.0, group=group, post=True)
        esperado = payment.amount_company_currency
        group.lines_rate = 1600.0
        self.assertAlmostEqual(payment.exchange_rate, 1520.0, 6)
        self.assertAlmostEqual(payment.amount_company_currency, esperado, 2)
        self.assertAlmostEqual(self._liquidity_balance(payment), esperado, 2)

    def test_agregar_factura_no_recotiza_el_grupo(self):
        """ El onchange solo propone cotización cuando todavía no hay una: si el
        grupo ya tiene una cotización pactada, agregar o quitar una factura no la
        pisa con la del día. """
        if not self._has_group_currency_module():
            self.skipTest('account_payment_group_currency no está instalado')
        Group = self.env['account.payment.group']
        base_vals = {
            'company_id': self.company.id,
            'partner_type': 'supplier',
            'partner_id': self.partner.id,
            'payment_date': self.payment_date,
            'lines_same_currency_id': self.foreign_currency.id,
        }
        pactada = Group.new(dict(base_vals, lines_rate=1520.0))
        pactada.onchange_lines_same_currency_id()
        self.assertAlmostEqual(pactada.lines_rate, 1520.0, 6)

        vacia = Group.new(dict(base_vals, lines_rate=0.0))
        vacia.onchange_lines_same_currency_id()
        self.assertAlmostEqual(vacia.lines_rate, self.rate_value, 4)

    # ------------------------------------------------------------------
    # grupo con líneas en más de una moneda
    # ------------------------------------------------------------------

    def _group_multimoneda(self, lines_rate=1520.0):
        group = self._new_group()
        group.write({
            'lines_same_currency_id': self.foreign_currency.id,
            'lines_rate': lines_rate,
        })
        return group

    def test_lines_rate_no_se_propaga_a_otra_moneda(self):
        """ Cambiar la cotización del encabezado propone a las líneas EN esa
        moneda. La línea en otra moneda conserva la suya: si se la pisara, se
        valuaría a la cotización de una moneda que no es la suya y el asiento se
        armaría con ese valor, sin ningún error. """
        if not self._has_group_currency_module():
            self.skipTest('account_payment_group_currency no está instalado')
        group = self._group_multimoneda()
        en_moneda_grupo = self._new_payment(
            1000.0, exchange_rate=1520.0, group=group)
        en_otra_moneda = self._new_payment(
            500.0, exchange_rate=self.rate_value_alt,
            currency=self.foreign_currency_alt, group=group)

        group.lines_rate = 1600.0
        group._onchange_lines_rate_propose_to_payments()

        self.assertAlmostEqual(en_moneda_grupo.exchange_rate, 1600.0, 6)
        self.assertAlmostEqual(
            en_otra_moneda.exchange_rate, self.rate_value_alt, 6,
            "La línea fuera de la moneda del encabezado no debe recotizarse")
        self.assertAlmostEqual(
            en_otra_moneda.amount_company_currency,
            self.company_currency.round(500.0 * self.rate_value_alt), 2)

    def test_hook_devuelve_la_del_dia_para_otra_moneda(self):
        if not self._has_group_currency_module():
            self.skipTest('account_payment_group_currency no está instalado')
        group = self._group_multimoneda()

        en_moneda_grupo = self._new_payment(1000.0, group=group)
        self.assertTrue(en_moneda_grupo._group_rate_applies())
        self.assertAlmostEqual(
            en_moneda_grupo._get_payment_exchange_rate(), 1520.0, 6)

        en_otra_moneda = self._new_payment(
            500.0, currency=self.foreign_currency_alt, group=group)
        self.assertFalse(en_otra_moneda._group_rate_applies())
        self.assertAlmostEqual(
            en_otra_moneda._get_payment_exchange_rate(), self.rate_value_alt, 4)

    def test_asiento_de_linea_en_otra_moneda(self):
        """ El asiento de la línea fuera de la moneda del encabezado cierra con
        SU cotización, y el constraint no salta. """
        if not self._has_group_currency_module():
            self.skipTest('account_payment_group_currency no está instalado')
        group = self._group_multimoneda()
        payment = self._new_payment(
            500.0, exchange_rate=self.rate_value_alt,
            currency=self.foreign_currency_alt, group=group, post=True)

        esperado = self.company_currency.round(500.0 * self.rate_value_alt)
        self.assertAlmostEqual(payment.amount_company_currency, esperado, 2)
        self.assertAlmostEqual(self._liquidity_balance(payment), esperado, 2)
        self.assertNotAlmostEqual(
            payment.amount_company_currency,
            self.company_currency.round(500.0 * 1520.0), 2)
        payment._check_move_matches_payment()

    # ------------------------------------------------------------------
    # la cotización del encabezado sobrevive al guardado
    # ------------------------------------------------------------------

    def test_guardar_no_revierte_la_cotizacion_propuesta(self):
        """ Regresión reportada: al cambiar la cotización del encabezado la
        línea se actualizaba en pantalla y al guardar volvía al valor anterior.

        El cliente manda el importe en moneda de compañía viejo junto con la
        cotización nueva, y el inverse de amount_company_currency -que corre
        después de las escrituras directas- recalculaba exchange_rate a partir
        de ese importe. Entre los dos manda la cotización. """
        if not self._has_group_currency_module():
            self.skipTest('account_payment_group_currency no está instalado')
        group = self._group_multimoneda(lines_rate=1520.0)
        payment = self._new_payment(1000.0, exchange_rate=1520.0, group=group)
        ars_viejo = payment.amount_company_currency

        group.write({
            'lines_rate': 1600.0,
            'payment_ids': [(1, payment.id, {
                'exchange_rate': 1600.0,
                'amount_company_currency': ars_viejo,
            })],
        })

        self.assertAlmostEqual(payment.exchange_rate, 1600.0, 6)
        self.assertAlmostEqual(
            payment.amount_company_currency,
            self.company_currency.round(1000.0 * 1600.0), 2)

    def test_el_write_del_grupo_propaga_aunque_el_cliente_no_mande_la_linea(self):
        """ La propagación no puede depender de que el onchange vuelva del
        cliente: escribir lines_rate alcanza. La línea en otra moneda sigue
        intacta. """
        if not self._has_group_currency_module():
            self.skipTest('account_payment_group_currency no está instalado')
        group = self._group_multimoneda(lines_rate=1520.0)
        en_moneda_grupo = self._new_payment(
            1000.0, exchange_rate=1520.0, group=group)
        en_otra_moneda = self._new_payment(
            500.0, exchange_rate=self.rate_value_alt,
            currency=self.foreign_currency_alt, group=group)

        group.write({'lines_rate': 1600.0})

        self.assertAlmostEqual(en_moneda_grupo.exchange_rate, 1600.0, 6)
        self.assertAlmostEqual(
            en_otra_moneda.exchange_rate, self.rate_value_alt, 6)

    def test_cotizacion_a_mano_en_la_linea_no_se_pisa(self):
        """ Sin tocar lines_rate, una cotización cargada a mano en una línea se
        respeta: la propagación solo corre cuando cambia el encabezado. """
        if not self._has_group_currency_module():
            self.skipTest('account_payment_group_currency no está instalado')
        group = self._group_multimoneda(lines_rate=1520.0)
        payment = self._new_payment(1000.0, exchange_rate=1520.0, group=group)
        group.write({'payment_ids': [(1, payment.id, {'exchange_rate': 1234.0})]})
        self.assertAlmostEqual(payment.exchange_rate, 1234.0, 6)

    # ------------------------------------------------------------------
    # resincronización (D3)
    # ------------------------------------------------------------------

    def test_cambiar_cotizacion_resincroniza_el_asiento(self):
        payment = self._new_payment(3398.86, exchange_rate=1520.0)
        payment.exchange_rate = 1600.0
        esperado = self.company_currency.round(3398.86 * 1600.0)
        self.assertAlmostEqual(self._liquidity_balance(payment), esperado, 2)
        self.assertAlmostEqual(payment.amount_company_currency, esperado, 2)

    def test_hook_de_l10n_ar_ux_sigue_vivo(self):
        campos = self.env['account.payment']._get_trigger_fields_to_sincronize()
        self.assertIn('exchange_rate', campos)
        self.assertIn('amount', campos)
        if 'l10n_latam_check_payment_date' in self.env['account.payment']._fields:
            self.assertIn('l10n_latam_check_payment_date', campos)

    # ------------------------------------------------------------------
    # alta de la línea (regresión PG 552977)
    # ------------------------------------------------------------------

    def test_alta_con_familias_ars_y_fx_en_desacuerdo(self):
        """ Grupo de adelanto: `unreconciled_amount_currency` = 3398,86 y
        `unreconciled_amount` = 0. La línea tiene que salir en 3398,86. """
        if not self._has_group_currency_module():
            self.skipTest('account_payment_group_currency no está instalado')
        group = self._new_group()
        group.write({
            'lines_same_currency_id': self.foreign_currency.id,
            'lines_rate': 1520.0,
            'unreconciled_amount_currency': 3398.86,
            'unreconciled_amount': 0.0,
        })
        payment = self.env['account.payment'].new({
            'payment_group_id': group.id,
            'journal_id': self.bank_journal.id,
            'company_id': self.company.id,
        })
        payment.onchange_payment_group_id()
        self.assertEqual(payment.currency_id, self.foreign_currency)
        self.assertAlmostEqual(payment.amount, 3398.86, 2)
        self.assertAlmostEqual(payment.exchange_rate, 1520.0, 6)
        self.assertAlmostEqual(
            payment.amount_company_currency,
            self.company_currency.round(3398.86 * 1520.0), 2)

    def test_alta_sin_cotizacion_cargada_en_el_grupo(self):
        if not self._has_group_currency_module():
            self.skipTest('account_payment_group_currency no está instalado')
        group = self._new_group()
        group.write({
            'lines_same_currency_id': self.foreign_currency.id,
            'lines_rate': 0.0,
            'unreconciled_amount_currency': 3398.86,
        })
        payment = self.env['account.payment'].new({
            'payment_group_id': group.id,
            'journal_id': self.bank_journal.id,
            'company_id': self.company.id,
        })
        payment.onchange_payment_group_id()
        self.assertEqual(payment.currency_id, self.foreign_currency)
        self.assertAlmostEqual(payment.amount, 3398.86, 2)
        # sin cotización en el grupo se propone la del día
        self.assertAlmostEqual(payment.exchange_rate, self.rate_value, 4)

    # ------------------------------------------------------------------
    # ciclo de vida
    # ------------------------------------------------------------------

    def test_cambiar_fecha_no_recotiza(self):
        """ `date` no está en el depends a propósito: la cotización pactada no
        se pierde al mover la fecha contable. """
        payment = self._new_payment(3398.86, exchange_rate=1520.0)
        payment.date = fields.Date.from_string('2026-08-21')
        self.assertAlmostEqual(payment.exchange_rate, 1520.0, 6)
        self.assertAlmostEqual(
            payment.amount_company_currency,
            self.company_currency.round(3398.86 * 1520.0), 2)

    def test_postear_y_volver_a_borrador(self):
        payment = self._new_payment(3398.86, exchange_rate=1520.0, post=True)
        esperado = self.company_currency.round(3398.86 * 1520.0)
        self.assertAlmostEqual(payment.amount_company_currency, esperado, 2)
        payment.action_draft()
        self.assertEqual(payment.state, 'draft')
        self.assertAlmostEqual(payment.amount_company_currency, esperado, 2)

    def test_posteado_lee_del_asiento_no_de_la_cotizacion(self):
        """ Prueba que los desvíos históricos se corrigen SIN migración: el pago
        posteado muestra lo que dice el mayor. """
        payment = self._new_payment(1477.2515, exchange_rate=self.rate_value,
                                    post=True)
        liquidity = payment._seek_liquidity_lines()
        self.assertEqual(len(liquidity), 1)
        # forzamos por SQL un asiento divergente, como los históricos
        self.env.cr.execute(
            "UPDATE account_move_line SET debit = %s, credit = %s, balance = %s "
            "WHERE id = %s", (0.0, 711625.00, -711625.00, liquidity.id))
        liquidity.invalidate_cache()
        payment.invalidate_cache()
        self.assertAlmostEqual(payment.amount_company_currency, 711625.00, 2)

    def test_pago_en_moneda_de_compania_no_se_toca(self):
        payment = self._new_payment(1000.0, currency=self.company_currency)
        self.assertFalse(payment.other_currency)
        self.assertAlmostEqual(payment.amount_company_currency, 1000.0, 2)
        payment.action_post()
        self.assertAlmostEqual(self._liquidity_balance(payment), 1000.0, 2)

    # ------------------------------------------------------------------
    # campo deprecado
    # ------------------------------------------------------------------

    def test_force_amount_no_afecta_el_asiento(self):
        payment = self._new_payment(3398.86, exchange_rate=1520.0)
        self.env.cr.execute(
            "UPDATE account_payment SET force_amount_company_currency = %s "
            "WHERE id = %s", (999999.0, payment.id))
        payment.invalidate_cache()
        payment.action_post()
        esperado = self.company_currency.round(3398.86 * 1520.0)
        self.assertAlmostEqual(self._liquidity_balance(payment), esperado, 2)
        self.assertAlmostEqual(payment.amount_company_currency, esperado, 2)

    # ------------------------------------------------------------------
    # red de seguridad
    # ------------------------------------------------------------------

    def test_constraint_detecta_asiento_incoherente(self):
        payment = self._new_payment(1477.2515, exchange_rate=self.rate_value,
                                    post=True)
        liquidity = payment._seek_liquidity_lines()
        self.env.cr.execute(
            "UPDATE account_move_line SET debit = %s, credit = %s, balance = %s "
            "WHERE id = %s", (0.0, 711625.00, -711625.00, liquidity.id))
        liquidity.invalidate_cache()
        payment.invalidate_cache()
        with self.assertRaises(ValidationError):
            payment._check_move_matches_payment()

    # ------------------------------------------------------------------
    # performance
    # ------------------------------------------------------------------

    def test_compute_batcheado_sobre_muchos_pagos(self):
        """ El compute lee el asiento por registro, así que el riesgo es que
        haga N+1. Medido sobre pagos FX posteados reales con caché fría: 49
        consultas para 20 pagos y 49 para 200 -- constante, el prefetch del ORM
        lo batchea. El número exacto depende del set de datos, así que se
        verifica la propiedad que importa: el costo total no crece con el lote.
        """
        Payment = self.env['account.payment']
        self.env.cr.execute("""
            SELECT ap.id FROM account_payment ap
            JOIN account_move am ON am.id = ap.move_id
            JOIN res_company co ON co.id = am.company_id
            WHERE ap.currency_id <> co.currency_id AND am.state = 'posted'
            ORDER BY ap.id DESC LIMIT 400
        """)
        ids = [row[0] for row in self.env.cr.fetchall()]
        if len(ids) < 220:
            self.skipTest('No hay suficientes pagos FX posteados para medir')

        def _medir(subset):
            self.env.registry.clear_caches()
            self.env.cache.invalidate()
            recs = Payment.browse(subset)
            antes = self.env.cr.sql_log_count
            recs.mapped('amount_company_currency')
            return self.env.cr.sql_log_count - antes

        chico = _medir(ids[:20])
        grande = _medir(ids[200:400])
        self.assertLessEqual(
            grande, max(chico, 1) * 2,
            "El compute de amount_company_currency no está batcheando: "
            "%s consultas para 200 pagos vs %s para 20" % (grande, chico))
