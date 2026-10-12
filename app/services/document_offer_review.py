from datetime import date
from decimal import Decimal, InvalidOperation

from app.models import PurchaseRequestItem, SupplierOffer, Supplier, utc_now
from app.services.price_search_service import product_key


def document_eligibility(offer: SupplierOffer, item: PurchaseRequestItem, supplier: Supplier) -> tuple[bool, str]:
    history = offer.review_history_json or []
    review = history[-1] if history else {}
    if not supplier.active:
        return False, 'Fornecedor inactivo.'
    if review.get('action') != 'save':
        return False, 'Validação da proforma pendente ou revogada.'
    if review.get('item_key') != product_key(item) or review.get('requested_quantity') != str(item.quantity):
        return False, 'O artigo ou a quantidade mudou. Revê novamente a proforma.'
    try:
        expiry = date.fromisoformat(review.get('valid_until') or '')
        coverage = Decimal(review.get('covered_quantity') or '0')
    except (ValueError, InvalidOperation):
        return False, 'Confirma validade e quantidade abrangida.'
    if expiry < utc_now().date():
        return False, 'Validade confirmada da proforma expirada.'
    if item.quantity is None or not coverage.is_finite() or coverage < item.quantity:
        return False, 'A quantidade abrangida não cobre o pedido.'
    if not item.unit or any(review.get(k) != value for k, value in {
        'match':'yes', 'unit':'yes', 'stock':'in', 'condition':'new', 'conditions':'yes'
    }.items()) or review.get('vat') not in {'included', 'excluded'}:
        return False, 'Confirma correspondência, unidade, IVA, condição nova, stock e condições comerciais.'
    return True, f'Validada para esta quantidade até {expiry.strftime("%d/%m/%Y")} (UTC).'
