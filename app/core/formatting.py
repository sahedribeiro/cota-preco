from decimal import Decimal, ROUND_HALF_UP, localcontext


def money(value: Decimal | float | int | None, currency: str = 'AOA') -> str:
    if value is None:
        return 'Por indicar'
    with localcontext() as context:
        context.prec = 40
        amount = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    formatted = format(amount, ',.2f').translate(str.maketrans({',': '.', '.': ','}))
    return f"{formatted} {'Kz' if currency == 'AOA' else currency}"
