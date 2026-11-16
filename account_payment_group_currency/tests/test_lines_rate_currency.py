# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""
La cotizacion del grupo (lines_rate) esta en moneda de la compania.

Se expresa como unidades de company_id.currency_id por 1 unidad de
lines_same_currency_id. Antes colgaba de currency_id del grupo, que queda con
la compania activa al crearlo y no se sincroniza con company_id: una Pre-OP de
una compania en ARS mostraba "USD" en la cotizacion y la redondeaba a 6
decimales, y una de una compania de USA la redondeaba a 2 (0,138912 USD por
CNY quedaba en 0,14).

Las companias y las cotizaciones se arman en el test, con cotizaciones propias
de cada compania, para no depender de la configuracion de la base.
"""
from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLinesRateCurrency(TransactionCase):

    # ARS por 1 FXC (hace de CNY) y por 1 FXE (hace de EUR) en la compania ARS
    ARS_PER_FXC = 205.0
    ARS_PER_FXE = 1500.0
    # USD por 1 FXC en la compania de USA
    USD_PER_FXC = 0.138912

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Currency = cls.env['res.currency'].with_context(active_test=False)
        cls.ars = Currency.search([('name', '=', 'ARS')], limit=1)
        cls.usd = Currency.search([('name', '=', 'USD')], limit=1)
        cls.ars.active = cls.usd.active = True
        # Mismo redondeo que el USD de la base real (0.000001). Una base
        # limpia lo trae en 0.01 y entonces el caso de USA no se distingue.
        cls.usd.rounding = 0.000001
        cls.date = fields.Date.from_string('2026-11-12')

        cls.fxc = cls.env['res.currency'].create({
            'name': 'FXC', 'symbol': 'FXC', 'rounding': 0.000001})
        cls.fxe = cls.env['res.currency'].create({
            'name': 'FXE', 'symbol': 'FXE', 'rounding': 0.01})

        cls.company_ars = cls.env['res.company'].create({
            'name': 'FX Test ARS', 'currency_id': cls.ars.id})
        cls.company_usd = cls.env['res.company'].create({
            'name': 'FX Test USD', 'currency_id': cls.usd.id})
        cls.env.user.company_ids |= cls.company_ars | cls.company_usd

        rates = [
            # moneda de cada compania en 1 para esa compania
            (cls.company_ars, cls.ars, 1.0),
            (cls.company_ars, cls.usd, 1.0 / 1000.0),
            (cls.company_ars, cls.fxc, 1.0 / cls.ARS_PER_FXC),
            (cls.company_ars, cls.fxe, 1.0 / cls.ARS_PER_FXE),
            (cls.company_usd, cls.usd, 1.0),
            (cls.company_usd, cls.ars, 1000.0),
            (cls.company_usd, cls.fxc, 1.0 / cls.USD_PER_FXC),
        ]
        for company, currency, rate in rates:
            cls.env['res.currency.rate'].create({
                'name': cls.date,
                'company_id': company.id,
                'currency_id': currency.id,
                'rate': rate,
            })
        cls.partner = cls.env['res.partner'].create({'name': 'FX Rate Partner'})

    def _new_group(self, company, currency, **vals):
        """ Grupo en memoria, como en el form. currency_id va a proposito
        distinto de la moneda de la compania cuando el test lo pide: es el
        dato que tienen las Pre-OP reportadas. """
        values = {
            'company_id': company.id,
            'currency_id': currency.id,
            'partner_type': 'supplier',
            'partner_id': self.partner.id,
            'payment_date': self.date,
        }
        values.update(vals)
        return self.env['account.payment.group'].with_company(company).new(
            values)

    def test_moneda_de_la_cotizacion_es_la_de_la_compania(self):
        field = self.env['account.payment.group']._fields['lines_rate']
        self.assertEqual(
            field.get_currency_field(self.env['account.payment.group']),
            'company_currency_id')
        group = self._new_group(self.company_ars, self.usd)
        self.assertEqual(group.company_currency_id, self.ars)
        group = self._new_group(self.company_usd, self.ars)
        self.assertEqual(group.company_currency_id, self.usd)

    def test_compania_ars_redondea_en_pesos_aunque_el_grupo_este_en_usd(self):
        """ El caso reportado: compania en ARS, pago en CNY, currency_id del
        grupo en USD. La cotizacion pactada se carga en pesos por CNY. """
        group = self._new_group(self.company_ars, self.usd,
                                lines_same_currency_id=self.fxc.id)
        group.lines_rate = 205.3456
        self.assertAlmostEqual(group.lines_rate, 205.35, 6)

    def test_compania_usd_conserva_seis_decimales_aunque_el_grupo_este_en_ars(self):
        group = self._new_group(self.company_usd, self.ars,
                                lines_same_currency_id=self.fxc.id)
        group.lines_rate = 0.138912
        self.assertAlmostEqual(group.lines_rate, 0.138912, 6)

    def test_compania_usd_propone_usd_por_unidad_del_pago(self):
        group = self._new_group(self.company_usd, self.ars,
                                lines_same_currency_id=self.fxc.id)
        group.onchange_lines_same_currency_id()
        self.assertAlmostEqual(group.lines_rate, self.USD_PER_FXC, 6)

    def test_cambiar_moneda_del_pago_repropone_y_no_cambia_la_del_tc(self):
        group = self._new_group(self.company_ars, self.usd,
                                lines_same_currency_id=self.fxc.id)
        group.onchange_lines_same_currency_id()
        self.assertAlmostEqual(group.lines_rate, self.ARS_PER_FXC, 2)

        # la cotizacion pactada con el banco se puede cargar a mano
        group.lines_rate = 210.0
        self.assertAlmostEqual(group.lines_rate, 210.0, 2)

        # otra moneda de pago: se propone la de esa moneda, no queda la
        # pactada para la anterior
        group.lines_same_currency_id = self.fxe
        group.onchange_lines_same_currency_id()
        self.assertAlmostEqual(group.lines_rate, self.ARS_PER_FXE, 2)

        group.lines_same_currency_id = self.usd
        group.onchange_lines_same_currency_id()
        self.assertAlmostEqual(group.lines_rate, 1000.0, 2)

        self.assertEqual(group.company_currency_id, self.ars)

    def test_pago_en_la_moneda_de_la_compania(self):
        group = self._new_group(self.company_usd, self.usd,
                                lines_same_currency_id=self.usd.id)
        group.onchange_lines_same_currency_id()
        self.assertAlmostEqual(group.lines_rate, 1.0, 6)

    def test_sin_moneda_de_pago_no_hay_cotizacion(self):
        group = self._new_group(self.company_ars, self.ars,
                                lines_same_currency_id=self.fxc.id,
                                lines_rate=210.0)
        group.lines_same_currency_id = False
        group.onchange_lines_same_currency_id()
        self.assertEqual(group.lines_rate, 0.0)

    def test_valor_guardado_se_lee_intacto(self):
        """ Las Pre-OP existentes no cambian: lo que esta en la base se lee tal
        cual, aunque tenga mas decimales que la moneda de la compania. """
        group = self.env['account.payment.group'].with_company(
            self.company_ars).create({
                'company_id': self.company_ars.id,
                'currency_id': self.usd.id,
                'partner_type': 'supplier',
                'partner_id': self.partner.id,
                'payment_date': self.date,
            })
        self.env.cr.execute(
            "UPDATE account_payment_group SET lines_rate = 1545.5123 "
            "WHERE id = %s", (group.id,))
        group.invalidate_cache(['lines_rate'])
        self.assertAlmostEqual(group.lines_rate, 1545.5123, 6)

    def test_create_redondea_igual_que_write(self):
        """ company_currency_id no viaja en los vals del create: sin el
        override, Odoo grababa la cotizacion sin redondear al crear y
        redondeada al editar. """
        group = self.env['account.payment.group'].with_company(
            self.company_ars).create({
                'company_id': self.company_ars.id,
                'currency_id': self.usd.id,
                'partner_type': 'supplier',
                'partner_id': self.partner.id,
                'payment_date': self.date,
                'lines_same_currency_id': self.fxc.id,
                'lines_rate': 205.3456,
            })
        group.invalidate_cache(['lines_rate'])
        self.assertAlmostEqual(group.lines_rate, 205.35, 6)
        group.lines_rate = 205.3456
        group.invalidate_cache(['lines_rate'])
        self.assertAlmostEqual(group.lines_rate, 205.35, 6)
