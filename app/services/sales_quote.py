from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import ProformaImport, PurchaseRequestItem, SupplierOffer, Supplier
from app.services.product_wording import canonical_description, canonical_unit


def selling_price(cost: Decimal, percentage: Decimal, method: str) -> Decimal:
    if not cost.is_finite() or cost <= 0 or not percentage.is_finite() or percentage < 0 or percentage > 10000:
        raise ValueError('Custo ou percentual inválido.')
    with localcontext() as context:
        context.prec=40
        if method == 'markup': price=cost*(1+percentage/100)
        elif method == 'margin' and percentage < 100: price=cost/(1-percentage/100)
        else: raise ValueError('Margem sobre venda deve ser inferior a 100%.')
        if price >= Decimal('1e16'): raise ValueError('Preço de venda excede o limite.')
        return price.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)


def quote_options(session: Session, request_id: int) -> list[dict]:
    options=[]
    imports=list(session.scalars(select(ProformaImport).where(ProformaImport.purchase_request_id==request_id)))
    for record in imports:
        for index,line in enumerate(record.draft_json['lines']):
            try: price=Decimal(line.get('unit_price') or '')
            except InvalidOperation: continue
            if not price.is_finite() or not 0 < price < Decimal('1e16') or price != price.quantize(Decimal('.01')): continue
            currency=line.get('currency')
            if not currency or len(currency)!=3 or not currency.isalpha(): continue
            options.append(dict(key=f'doc:{record.id}:{index}',label=record.draft_json.get('supplier_name') or f'Fornecedor por identificar · proforma #{record.id}',
                description=line['description'],reference=line.get('reference'),unit=line.get('unit'),cost=str(price),currency=currency,
                vat=line.get('vat_included'),evidence=line.get('evidence'),document_id=record.id,item_id=None))
    items=select(PurchaseRequestItem.id).where(PurchaseRequestItem.purchase_request_id==request_id)
    for offer in session.scalars(select(SupplierOffer).where(SupplierOffer.purchase_request_item_id.in_(items))):
        if offer.document_line_key or offer.unit_price is None or offer.currency=='UNK' or offer.unit_price<=0: continue
        supplier=session.get(Supplier,offer.supplier_id)
        options.append(dict(key=f'offer:{offer.id}',label=supplier.name,description=offer.product_name,
            reference=None,unit=None,cost=str(offer.unit_price),currency=offer.currency,vat=offer.vat_included,
            evidence=offer.product_url,item_id=offer.purchase_request_item_id,observed_at=offer.observed_at.isoformat()))
    return options


def suggested_options(item: PurchaseRequestItem, options: list[dict]) -> list[dict]:
    return [o for o in options if o['item_id']==item.id or (
        canonical_description(o['description']) in {canonical_description(item.description),canonical_description(item.normalized_description or '')}
        and (not o['reference'] or not item.reference or o['reference']==item.reference)
        and (not o['unit'] or not item.unit or canonical_unit(o['unit'])==canonical_unit(item.unit)))]
