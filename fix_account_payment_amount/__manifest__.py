{
    'name': 'Fix Conversión de Pago',
    'version': '1.0',
    'author': 'NovaTech-PhinxLab.',
    'maintainer': 'NovaTech-PhinxLab.',
    'complexity': 'easy',
    'category': 'Hidden',
    'description': """
Quicksight Reporting
==================
""",
    'depends': [
        'currency_rate_add_percent'
    ],
    'data': [
        "views/account_payment_view.xml"
    ],
    'assets': {
        'web.assets_backend': [
        ],
        'web.assets_qweb': [
        ],
    },
    'website': 'http://www.novatech.com.ar',
    'installable': True
}
