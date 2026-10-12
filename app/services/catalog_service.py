from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import SupplierProduct, PriceRecord, utc_now
from app.core.config import settings


def amount(value: object, decimal_separator: str='.') -> Decimal | None:
    if value is None or str(value).strip()=='': return None
    text=str(value).strip().replace(' ','').replace('\xa0','')
    if decimal_separator==',': text=text.replace('.','').replace(',','.')
    try: result=Decimal(text)
    except InvalidOperation: return None
    if not result.is_finite() or not 0<result<Decimal('1e16') or result.as_tuple().exponent < -2: return None
    return result


def date_value(value: object, end: bool=False) -> datetime | None:
    if not value: return None
    if 'T' in str(value) or ' ' in str(value):
        try:
            parsed=datetime.fromisoformat(str(value))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        except ValueError: pass
    for fmt in ('%Y-%m-%d','%d/%m/%Y','%d-%m-%Y','%d.%m.%Y'):
        try:
            result=datetime.strptime(str(value).strip(),fmt).replace(tzinfo=timezone.utc)
            return result.replace(hour=23,minute=59,second=59) if end else result
        except ValueError: pass
    return None


def add_price(session: Session, supplier_id: int, line: dict, origin_key: str, source_kind: str, source_reference: str,
              price_date: object=None, valid_until: object=None) -> PriceRecord:
    existing=session.scalar(select(PriceRecord).where(PriceRecord.origin_key==origin_key))
    if existing: return existing
    description=str(line.get('description') or '').strip()
    identity=hashlib.sha256(f'{supplier_id}|{description}|{line.get("reference") or ""}|{line.get("unit") or ""}'.encode()).hexdigest()
    product=session.scalar(select(SupplierProduct).where(SupplierProduct.identity_key==identity))
    if not product:
        product=SupplierProduct(supplier_id=supplier_id,description=description or 'Descrição por confirmar',reference=line.get('reference'),unit=line.get('unit'),identity_key=identity)
        session.add(product);session.flush()
    price=amount(line.get('unit_price'))
    currency=str(line.get('currency') or '').upper()
    currency=currency if len(currency)==3 and currency.isalpha() and currency!='UNK' else None
    vat=line.get('vat_included') if isinstance(line.get('vat_included'),bool) else None
    rate=amount(line.get('vat_rate'))
    if str(line.get('vat_rate')) in {'0','0.0','0.00'}: rate=Decimal('0')
    if rate is not None and rate>100: rate=None
    status='accepted' if description and price and currency and line.get('confidence',100)>=85 else 'review'
    if (price_date and not date_value(price_date)) or (valid_until and not date_value(valid_until,True)): status='review'
    record=PriceRecord(supplier_product_id=product.id,unit_price=price,currency=currency,vat_included=vat,vat_rate=rate,
        price_date=date_value(price_date),valid_until=date_value(valid_until,True),source_kind=source_kind,source_reference=source_reference,
        evidence=str(line.get('evidence') or ''),origin_key=origin_key,status=status,raw_json=line)
    session.add(record);session.flush()
    return record


def price_state(record: PriceRecord) -> str:
    def utc(value): return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if record.status!='accepted': return 'Rever'
    if record.valid_until and utc(record.valid_until)<utc_now(): return 'Expirado'
    if not record.price_date: return 'Data desconhecida'
    if utc(record.price_date)>utc_now(): return 'Data futura'
    if utc(record.price_date)<utc_now()-timedelta(days=settings.catalog_stale_days): return 'Desactualizado'
    return 'Actual'


def current_price(records: list[PriceRecord]) -> PriceRecord | None:
    eligible=[r for r in records if r.status=='accepted' and price_state(r) not in {'Expirado','Data futura'}]
    return max(eligible,key=lambda r: ((r.price_date or r.imported_at).replace(tzinfo=timezone.utc),r.id)) if eligible else None
