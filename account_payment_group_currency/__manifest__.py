{
    "name": "Account Payment with Multiple methods and MultiCurrency",
    "version": "15.0.1.0.0",
    "category": "Accounting",
    # "website": "",
    "author": "AXCELERE",
    "license": "AGPL-3",
    "application": False,
    'installable': True,
    "external_dependencies": {
        "python": [],
        "bin": [],
    },
    "depends": [
        "account_payment_group",
        "account_withholding_automatic"
    ],
    "data": [
        'views/account_payment_group_view.xml',
    ],
    "demo": [
    ],
}
