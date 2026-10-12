from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.models import PurchaseRequestItem
from app.services.comparison_service import comparison_values


def purchase_summary(items: list[PurchaseRequestItem], results: dict) -> dict:
    rows = []
    totals: dict[tuple[str, bool], Decimal] = {}
    for item in items:
        result = results.get(item.id)
        offer = result['winner'] if result and not result.get('comparison_stale', result['stale']) else None
        total = None
        reason = result['reason'] if result else 'Ainda não pesquisado.'
        vat = comparison_values(offer)['vat'] if offer else None
        if offer and item.quantity is not None:
            with localcontext() as context:
                context.prec = 40
                total = (offer.unit_price * item.quantity).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                key = (offer.currency, vat)
                totals[key] = totals.get(key, Decimal('0')) + total
        elif offer:
            reason = 'Quantidade por indicar. ' + reason
        rows.append({'item': item, 'offer': offer, 'total': total, 'vat': vat,
                     'origin': ('Proforma' if offer.document_line_key else 'Website') if offer else '',
                     'supplier': result['names'].get(offer.supplier_id, '') if offer else '',
                     'partial': bool(result and result.get('search') and result['search'].status == 'partial'), 'reason': reason})
    return {'rows': rows, 'totals': totals, 'counted': sum(row['total'] is not None for row in rows),
            'pending': sum(row['total'] is None for row in rows),
            'partial': any(row['partial'] for row in rows)}
