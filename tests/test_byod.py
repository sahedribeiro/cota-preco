import json
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PurchaseRequest, PurchaseRequestItem, Supplier, SupplierOffer
from app.services.comparison_service import cheapest_per_item, match_product
from app.services.price_search_service import PriceSearchService
from app.services.product_parser import parse_products
import app.services.price_search_service as search_module
from tests.test_database import migrated_db

BASE = 'https://store.byod.it.ao'
DATA = json.loads((Path(__file__).parent / 'fixtures/byod-product.json').read_text(encoding='utf-8'))
PRODUCT_URL = DATA['offers']['url']
PRODUCT_HTML = '<script type="application/ld+json">' + json.dumps(DATA) + '</script>'


def requested_item() -> PurchaseRequestItem:
    return PurchaseRequestItem(description='Portátil HP Laptop 15', brand='HP', model='HP Laptop 15',
                               reference='HP-2026-00004', quantity=2, unit='unidade', specifications_json={'RAM': '16GB'})


def test_real_markup_brand_entities_and_unknowns():
    products, _ = parse_products(PRODUCT_HTML, PRODUCT_URL)
    product = products[0]
    assert product['name'].startswith('Portátil HP')
    assert product['brand'] == 'HP' and product['model'] == 'HP Laptop 15'
    assert product['price'] == '1200400.00' and product['currency'] == 'AOA'
    assert product['vat'] is None and product['unit'] == '' and product['condition'] == ''
    request = requested_item()
    assert match_product(request, product)[0] == 74
    request.specifications_json = {'RAM': '8GB'}
    assert match_product(request, product)[0] == 0
    other, _ = parse_products(PRODUCT_HTML.replace(BASE, 'https://example.com'), 'https://example.com/product/a')
    assert other[0]['brand'] == 'Loja HP'


def test_brand_as_reference_uses_model_without_accepting_wrong_specs(migrated_db, monkeypatch):
    engine, _ = migrated_db
    urls = []
    def fetch(url):
        urls.append(url)
        return PRODUCT_HTML
    monkeypatch.setattr(search_module, 'read_allowed_html', fetch)
    with Session(engine) as session:
        purchase = PurchaseRequest(title='Referência incompleta')
        supplier = Supplier(name='BYOD', website=BASE+'/', active=True, scraping_enabled=True,
                            search_url_template=BASE+'/produtos?pesquisa={query}')
        session.add_all([purchase, supplier]); session.flush()
        item = requested_item(); item.reference = 'HP'; item.purchase_request_id = purchase.id
        session.add(item); session.flush()
        result = PriceSearchService().search(session, item, [supplier])
        assert result.search_query == 'HP Laptop 15'
        assert urls == [BASE+'/produtos?pesquisa=HP+Laptop+15']
        assert result.raw_result_json['offers'][0]['confidence'] == 74
        assert item.reference == 'HP'
        product = parse_products(PRODUCT_HTML, PRODUCT_URL)[0][0]
        item.specifications_json = {'RAM': '8GB'}
        assert match_product(item, product)[0] == 0
        item.specifications_json = {'RAM': '16GB'}
        item.reference = 'WRONG-123'
        assert match_product(item, product)[0] == 0


def test_search_detail_persistence_and_cache(migrated_db, monkeypatch):
    engine, _ = migrated_db
    calls = []
    search_url = BASE + '/produtos?pesquisa=HP-2026-00004'

    def fetch(url: str) -> str:
        calls.append(url)
        if url == search_url:
            return f'<a href="/produtos/comparar">Comparar</a><a href="{PRODUCT_URL}">Produto</a>'
        assert url == PRODUCT_URL
        return PRODUCT_HTML

    monkeypatch.setattr(search_module, 'read_allowed_html', fetch)
    with Session(engine) as session:
        purchase = PurchaseRequest(title='Teste isolado BYOD')
        supplier = Supplier(name='BYOD IT Store', website=BASE + '/', active=True, scraping_enabled=True,
                            search_url_template=BASE + '/produtos?pesquisa={query}')
        session.add_all([purchase, supplier])
        session.flush()
        item = requested_item()
        item.purchase_request_id = purchase.id
        session.add(item)
        session.flush()
        service = PriceSearchService()
        search = service.search(session, item, [supplier])
        offer = session.scalar(select(SupplierOffer).where(SupplierOffer.price_search_id == search.id))
        assert offer.unit_price == Decimal('1200400.00')
        assert offer.product_url == PRODUCT_URL and offer.match_confidence == 74
        assert cheapest_per_item([offer])[0] is None
        cached = service.search(session, item, [supplier])
        assert cached.raw_result_json['cache_hit']
        assert calls == [search_url, PRODUCT_URL]
