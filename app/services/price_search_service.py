import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote_plus, urlsplit
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models import PriceSearch, PurchaseRequestItem, Supplier, SupplierOffer, utc_now
from app.services.comparison_service import normalize
from app.services.product_parser import parse_products
from app.services.comparison_service import match_product, usable_reference
from app.services.website_service import read_allowed_html, WebsiteError
from app.services.catalog_service import add_price


def product_key(item: PurchaseRequestItem) -> str:
    values = {key: normalize(getattr(item,key) or '') for key in ('description','normalized_description','brand','model','reference','unit')}
    values['specs'] = {normalize(k): normalize(v) for k,v in (item.specifications_json or {}).items()}
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


def cache_key(item: PurchaseRequestItem, suppliers: list[Supplier]) -> str:
    sources = [(s.id,s.website,s.search_url_template,s.active,s.scraping_enabled) for s in sorted(suppliers,key=lambda s:s.id)]
    return hashlib.sha256(('v4'+product_key(item)+json.dumps(sources)).encode()).hexdigest()


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class PriceSearchService:
    def search(self, session: Session, item: PurchaseRequestItem, suppliers: list[Supplier], refresh: bool = False) -> PriceSearch:
        if not suppliers or len(suppliers) > 5:
            raise ValueError('Selecciona entre 1 e 5 fornecedores.')
        if any(not s.active or not s.scraping_enabled or not s.website for s in suppliers):
            raise ValueError('Todos os fornecedores devem estar activos e permitir leitura.')
        key = cache_key(item, suppliers)
        now = utc_now()
        cached = None if refresh else session.scalar(select(PriceSearch).where(PriceSearch.cache_key == key, PriceSearch.expires_at > now, PriceSearch.status == 'completed').order_by(PriceSearch.id.desc()))
        query = item.normalized_description or item.description
        if usable_reference(item):
            query = item.reference
        elif item.model:
            query = item.model if normalize(item.model).startswith(normalize(item.brand or '')) else ' '.join(filter(None, [item.brand, item.model]))
        query = query[:500]
        if cached:
            raw = dict(cached.raw_result_json)
            raw['cache_hit'] = True
            expires = cached.expires_at
        else:
            raw = {'item_key':product_key(item), 'supplier_ids':[s.id for s in suppliers], 'offers':[], 'sources':[], 'cache_hit':False}
            for supplier in suppliers:
                source = {'supplier_id':supplier.id,'name':supplier.name}
                url = supplier.search_url_template.replace('{query}',quote_plus(query)) if supplier.search_url_template else supplier.website
                source['url'] = url
                try:
                    if urlsplit(url).hostname != urlsplit(supplier.website).hostname:
                        raise WebsiteError('O URL de pesquisa deve usar o mesmo domínio do website.')
                    html = read_allowed_html(url)
                    products, links = parse_products(html, url)
                    page_errors = []
                    if not products:
                        for link in links:
                            try:
                                page_products, _ = parse_products(read_allowed_html(link),link)
                                products.extend(page_products)
                            except WebsiteError as error:
                                page_errors.append(str(error))
                    seen = set()
                    count = 0
                    for product in products:
                        signature = (product['url'],product['name'],product['price'])
                        if signature in seen: continue
                        seen.add(signature)
                        confidence, reasons = match_product(item, product)
                        if confidence < 50: continue
                        raw['offers'].append(dict(product, supplier_id=supplier.id, confidence=confidence, reasons=reasons, observed_at=utc_now().isoformat()))
                        count += 1
                    source['status'] = 'found' if count else 'not_found'
                    source['message'] = f'{count} ofertas candidatas.' if count else 'Nenhuma oferta compatível encontrada nas páginas lidas. O site pode precisar de suporte adicional; isto não confirma falta de stock.'
                    if page_errors:
                        source['status'] = 'error'
                        source['message'] += ' Algumas páginas falharam: ' + '; '.join(dict.fromkeys(page_errors))
                except WebsiteError as error:
                    source.update(status='error',message=str(error))
                raw['sources'].append(source)
            expires = now + timedelta(hours=settings.price_search_cache_hours)
        status = 'partial' if any(s['status']=='error' for s in raw['sources']) else 'completed'
        search = PriceSearch(purchase_request_item_id=item.id,search_query=query,cache_key=key,searched_at=now,expires_at=expires,status=status,raw_result_json=raw)
        session.add(search); session.flush()
        for product in raw['offers']:
            session.add(SupplierOffer(purchase_request_item_id=item.id,supplier_id=product['supplier_id'],price_search_id=search.id,
                product_name=product['name'],product_url=product['url'],unit_price=Decimal(product['price']) if product['price'] else None,
                currency=product['currency'],vat_included=product['vat'],availability=product['availability'],
                observed_at=datetime.fromisoformat(product['observed_at']), match_confidence=product['confidence'],evidence_json=product))
        for product in raw['offers']:
            origin=hashlib.sha256(json.dumps([product['supplier_id'],product['url'],product['name'],product['observed_at'],product['price']]).encode()).hexdigest()
            add_price(session,product['supplier_id'],dict(description=product['name'],reference=product.get('reference'),unit=product.get('unit'),
                unit_price=product['price'],currency=product['currency'],vat_included=product['vat'],evidence=product['url']),
                'website:'+origin,'website',product['url'],product['observed_at'][:10])
        session.commit()
        return search
