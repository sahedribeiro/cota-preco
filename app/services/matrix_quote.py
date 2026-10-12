from decimal import Decimal
from app.services.procurement_matrix import number, rounded
from app.services.sales_quote import selling_price


def calculate_quote(report: dict, options: dict) -> dict:
    strategy = options.get('strategy')
    markup = number(options.get('markup', '0'))
    vat = number(options.get('vat', '14'))
    if markup is None or markup > 10000 or vat not in {Decimal(0), Decimal(14)}:
        raise ValueError('Markup ou IVA inválido.')
    if vat == 0 and not options.get('exemption', '').strip():
        raise ValueError('Indica a menção legal da isenção.')
    if strategy not in {'lowest', 'highest', 'average', 'manual'} and not str(strategy).startswith('supplier:'):
        raise ValueError('Escolhe uma estratégia de cotação.')
    lines = []
    currency = None
    for index, row in enumerate(report['rows']):
        if row.get('comparison_warning'):
            raise ValueError(f"Artigo {row['number']}: {row['comparison_warning']}")
        available = [(i, cell) for i, cell in enumerate(row['cells']) if cell and number(cell.get('compared_price')) is not None]
        if strategy in {'lowest', 'highest', 'average'}:
            available = [(i, cell) for i, cell in available if cell.get('currency') == row['currency']]
        if not available:
            raise ValueError(f"Artigo {row['number']}: falta preço. Completa a matriz antes de emitir.")
        if strategy in {'lowest', 'highest'}:
            pick = min if strategy == 'lowest' else max
            selected = pick(available, key=lambda pair: number(pair[1]['compared_price']))
            cost = number(selected[1]['compared_price'])
        elif strategy == 'average':
            selected = available[0]
            cost = rounded(sum(number(cell['compared_price']) for _, cell in available) / len(available))
        else:
            value = options.get('manual', {}).get(str(index)) if strategy == 'manual' else strategy.split(':', 1)[1]
            selected = next((pair for pair in available if str(pair[0]) == str(value)), None)
            if selected is None:
                raise ValueError(f"Artigo {row['number']}: escolhe um fornecedor com preço.")
            cost = number(selected[1]['compared_price'])
        row_currency = selected[1].get('currency')
        uncertain = any(cell.get('confidence', 100) < 85 for _, cell in available) if strategy == 'average' else selected[1].get('confidence', 100) < 85
        if uncertain:
            raise ValueError(f"Artigo {row['number']}: confirma o preço extraído antes de emitir.")
        if not row_currency or (currency and currency != row_currency):
            raise ValueError('A cotação requer uma única moeda confirmada ou assumida.')
        currency = row_currency
        quantity = number(row['quantity'])
        if not quantity or quantity > 999999999:
            raise ValueError(f"Artigo {row['number']}: confirma a quantidade.")
        sale_price = selling_price(cost, markup, 'markup')
        total = rounded(sale_price * quantity)
        lines.append(dict(number=row['number'], description=row['product'], unit=row['unit'], quantity=str(quantity),
            cost=str(cost), sale_price=str(sale_price), total=str(total), margin=str(total-rounded(cost*quantity))))
    subtotal = sum(Decimal(line['total']) for line in lines)
    tax = rounded(subtotal * vat / 100)
    margin = sum(Decimal(line['margin']) for line in lines)
    return dict(lines=lines, subtotal=str(subtotal), tax=str(tax), total=str(subtotal+tax),
                margin=str(margin), currency=currency, options=options)
