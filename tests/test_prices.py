import json
from decimal import Decimal
from datetime import timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy.orm import Session
from app.models import PurchaseRequest, PurchaseRequestItem, Supplier, SupplierOffer, PriceSearch, utc_now
from app.services.price_search_service import PriceSearchService
from app.services.product_parser import parse_products
from app.services.comparison_service import match_product, cheapest_per_item
from tests.test_database import migrated_db
import app.services.price_search_service as search_module
from app.services.website_service import WebsiteError
from tests.test_requests import client
import app.routes.requests as routes


def product(price='100.00', currency='AOA', ram='16 GB'):
    return {'@type':'Product','name':'HP PC','brand':{'name':'HP'},'model':'A1','mpn':'REF1',
        'additionalProperty':[{'name':'RAM','value':ram}],
        'offers':{'@type':'Offer','price':price,'priceCurrency':currency,'availability':'https://schema.org/InStock','itemCondition':'https://schema.org/NewCondition',
        'priceSpecification':{'unitText':'unidade','valueAddedTaxIncluded':True}}}


def html(data):
    return '<script type="application/ld+json">'+json.dumps(data)+'</script>'


def item():
    return PurchaseRequestItem(description='HP PC',normalized_description='HP PC',brand='HP',model='A1',reference='REF1',quantity=2,unit='unidade',specifications_json={'RAM':'16 GB'})


def test_parser_matching_and_no_guessed_prices():
    parsed, _ = parse_products(html(product()),'https://example.com/product/pc')
    assert parsed[0]['price'] == '100.00'
    assert match_product(item(),parsed[0])[0] == 98
    wrong, _ = parse_products(html(product(ram='8 GB')),'https://example.com/product/pc')
    assert match_product(item(),wrong[0])[0] == 0
    no_price, _ = parse_products(html(product(price='10.000,50')),'https://example.com/product/pc')
    assert no_price[0]['price'] is None
    assert no_price[0]['status'] != 'Sob consulta'
    missing = dict(parsed[0], specifications={})
    assert match_product(item(),missing)[0] == 74


def test_comparison_currency_vat_and_stock():
    def offer(price, **kwargs):
        values = dict(unit_price=Decimal(price),currency='AOA',vat_included=True,match_confidence=95,availability='InStock',evidence_json={'condition':'NewCondition'})
        values.update(kwargs)
        return SupplierOffer(**values)
    first, second = offer('100'), offer('90')
    assert cheapest_per_item([first,second])[0] is second
    assert cheapest_per_item([first,offer('50',currency='USD')])[0] is None
    assert cheapest_per_item([first,offer('50',vat_included=None)])[0] is None
    assert cheapest_per_item([offer('50',availability='OutOfStock')])[0] is None
    assert cheapest_per_item([offer('50',match_confidence=49)])[0] is None


def test_cache_cross_request_refresh_and_changed_specs(migrated_db, monkeypatch):
    engine, _ = migrated_db
    calls=[]
    def fetch(url):
        calls.append(url)
        return html(product())
    monkeypatch.setattr(search_module,'read_allowed_html',fetch)
    with Session(engine) as session:
        request=PurchaseRequest(title='Test'); supplier=Supplier(name='Test',website='https://example.com/',active=True,scraping_enabled=True)
        session.add_all([request,supplier]);session.flush()
        one=item();one.purchase_request_id=request.id;session.add(one);session.flush()
        service=PriceSearchService()
        first=service.search(session,one,[supplier])
        assert len(calls)==1 and not first.raw_result_json['cache_hit']
        two=item();two.purchase_request_id=request.id;two.quantity=5;session.add(two);session.flush()
        second=service.search(session,two,[supplier])
        assert len(calls)==1 and second.raw_result_json['cache_hit']
        assert second.expires_at == first.expires_at
        service.search(session,two,[supplier],refresh=True)
        assert len(calls)==2
        two.specifications_json={'RAM':'32 GB'}
        service.search(session,two,[supplier])
        assert len(calls)==3
        supplier.scraping_enabled=False
        with pytest.raises(ValueError): service.search(session,two,[supplier])


def test_expired_cache_and_failed_source(migrated_db, monkeypatch):
    engine, _ = migrated_db
    calls = []

    def fetch(url: str) -> str:
        calls.append(url)
        if len(calls) > 1:
            raise WebsiteError('Site indisponível')
        return html(product())

    monkeypatch.setattr(search_module, 'read_allowed_html', fetch)
    with Session(engine) as session:
        request = PurchaseRequest(title='Teste')
        supplier = Supplier(name='Teste', website='https://example.com/', active=True, scraping_enabled=True)
        session.add_all([request, supplier])
        session.flush()
        requested = item()
        requested.purchase_request_id = request.id
        session.add(requested)
        session.flush()
        service = PriceSearchService()
        first = service.search(session, requested, [supplier])
        first.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
        failed = service.search(session, requested, [supplier])
        assert failed.status == 'partial'
        assert failed.raw_result_json['sources'][0]['message'] == 'Site indisponível'
        service.search(session, requested, [supplier])
        assert len(calls) == 3


def test_price_route_render_cache_and_item_ownership(client, monkeypatch):
    calls = []

    def fetch(url: str) -> str:
        calls.append(url)
        return html(product())

    monkeypatch.setattr(search_module, 'read_allowed_html', fetch)
    with routes.SessionLocal() as session:
        purchase = PurchaseRequest(title='Compra')
        other = PurchaseRequest(title='Outra')
        supplier = Supplier(name='Loja de teste', website='https://example.com/', active=True, scraping_enabled=True)
        session.add_all([purchase, other, supplier])
        session.flush()
        requested = item()
        requested.purchase_request_id = purchase.id
        session.add(requested)
        session.commit()
        url = f'/requests/{purchase.id}/items/{requested.id}/search'
        wrong_url = f'/requests/{other.id}/items/{requested.id}/search'
        supplier_id = supplier.id

    response = client.post(url, data={'supplier_ids': supplier_id})
    assert response.status_code == 200
    assert 'Menor preço comparável' in response.text
    assert '200,00 Kz' in response.text
    assert 'Loja de teste' in response.text
    cached = client.post(url, data={'supplier_ids': supplier_id})
    assert 'Cache reutilizada' in cached.text and len(calls) == 1
    assert client.post(wrong_url, data={'supplier_ids': supplier_id}).status_code == 404
    assert client.post(url, data={'supplier_ids': 999999}).status_code == 422


def test_malformed_links_and_volume_prices():
    data = product()
    data['offers']['priceSpecification']['referenceQuantity'] = {'value': 10}
    parsed, links = parse_products(html(data) + '<a href="http://[invalid/product/a">bad</a><script type>bad</script>', 'https://example.com/')
    assert parsed[0]['restricted'] is True
    assert links == []


def test_partial_search_keeps_valid_offer_and_review(client, monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        if 'blocked.example' in url:
            raise WebsiteError('HTTP 403')
        return html(product())
    monkeypatch.setattr(search_module, 'read_allowed_html', fetch)
    with routes.SessionLocal() as session:
        purchase = PurchaseRequest(title='Consulta parcial')
        good = Supplier(name='OK', website='https://example.com/', active=True, scraping_enabled=True)
        bad = Supplier(name='Bloqueada', website='https://blocked.example/', active=True, scraping_enabled=True)
        session.add_all([purchase, good, bad]); session.flush()
        requested = item(); requested.purchase_request_id = purchase.id
        session.add(requested); session.commit()
        url = f'/requests/{purchase.id}/items/{requested.id}/search'
        ids = [good.id, bad.id]
        request_id = purchase.id
    response = client.post(url, data={'supplier_ids': ids})
    assert response.status_code == 200
    assert 'Comparação parcial' in response.text and 'Menor preço comparável</strong>' in response.text
    assert 'Desactualizado' not in response.text
    with routes.SessionLocal() as session:
        from sqlalchemy import select
        offer_id = session.scalar(select(SupplierOffer.id))
    reviewed = client.post(f'/requests/{request_id}/offers/{offer_id}/review', data={'reviewer':'Teste', 'notes':'Por confirmar'})
    assert reviewed.status_code == 200
    client.post(url, data={'supplier_ids': ids})
    assert len(calls) == 4  # Incomplete coverage must be retried, not cached as complete.


def test_querapido_product_paths_exclude_catalog_and_actions():
    html_page=''.join(f'<a href="{path}">Produto</a>' for path in ['/loja-2/', '/loja-2/informatica/pc-hp/', '/loja-2/informatica/pc-hp/?add-to-cart=1'])
    _,links=parse_products(html_page,'https://www.querapidoangola.com/?s=hp')
    assert links==['https://www.querapidoangola.com/loja-2/informatica/pc-hp/']


def test_product_page_failure_keeps_other_prices(migrated_db, monkeypatch):
    engine,_=migrated_db
    def read(url):
        if url.endswith('/product/broken'): raise WebsiteError('Falha temporária')
        if url.endswith('/product/good'): return html(product())
        return '<a href="/product/broken">Primeiro</a><a href="/product/good">Segundo</a>'
    monkeypatch.setattr(search_module,'read_allowed_html',read)
    with Session(engine) as session:
        purchase=PurchaseRequest(title='Teste'); session.add(purchase);session.flush()
        wanted=item();wanted.purchase_request_id=purchase.id
        supplier=Supplier(name='Loja',website='https://example.com',active=True,scraping_enabled=True)
        session.add_all([wanted,supplier]);session.flush()
        search=PriceSearchService().search(session,wanted,[supplier])
        assert search.status=='partial'
        assert len(search.raw_result_json['offers'])==1
        assert search.raw_result_json['offers'][0]['price']=='100.00'
