from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import select

from app.models import PurchaseRequestItem, Supplier, SupplierOffer, PriceSearch, utc_now
import app.routes.requests as routes
from tests.test_requests import client, save


def test_document_validation_comparison_and_revocation(client):
    save(client)
    with routes.SessionLocal() as session:
        item = session.scalar(select(PurchaseRequestItem))
        item.quantity = Decimal('2')
        item.unit = 'unidade'
        supplier = Supplier(name='Loja documental', active=True)
        session.add(supplier)
        session.flush()
        offer = SupplierOffer(purchase_request_item_id=item.id, supplier_id=supplier.id,
                              product_name='PC', unit_price=Decimal('90'), currency='AOA',
                              match_confidence=0, document_line_key='test:1',
                              evidence_json={'restricted':True, 'import_id':1, 'line_index':0})
        session.add(offer)
        session.commit()
        oid = offer.id
    url = f'/requests/1/offers/{oid}/document-review'
    def winner():
        with routes.SessionLocal() as session:
            item = session.get(PurchaseRequestItem, 1)
            return routes.load_results(session, [item])[1]['winner']
    assert winner() is None
    assert client.get('/requests/1').status_code == 200
    data = dict(reviewer='Ana', notes='Confirmado na proforma', match='yes', unit='yes', vat='included',
                condition='new', stock='in', conditions='yes', covered_quantity='2',
                valid_until=(utc_now().date()+timedelta(days=2)).isoformat())
    assert client.post(url, data=data).status_code == 200
    assert winner().id == oid
    book = load_workbook(BytesIO(client.get('/requests/1/export.xlsx').content))
    values = [c.value for row in book['Resumo'] for c in row]
    assert 'Proforma' in values and 180 in values
    for override in ({'covered_quantity':'1'}, {'valid_until':'2000-01-01'}, {'stock':'unknown'}, {'conditions':'unknown'}, {'action':'revoke'}, {'valid_until':'', 'covered_quantity':''}):
        assert client.post(url, data=data | override).status_code == 200
        assert winner() is None
    client.post(url, data=data)
    with routes.SessionLocal() as session:
        item = session.get(PurchaseRequestItem, 1)
        supplier = session.get(Supplier, 1)
        search = PriceSearch(purchase_request_item_id=1, cache_key=routes.cache_key(item, [supplier]),
                             search_query='PC', status='completed', expires_at=utc_now()+timedelta(hours=24),
                             raw_result_json={'supplier_ids':[supplier.id], 'sources':[]})
        session.add(search)
        session.flush()
        web = SupplierOffer(purchase_request_item_id=1, supplier_id=supplier.id, price_search_id=search.id,
                            product_name='PC web', unit_price=Decimal('100'), currency='AOA', vat_included=True,
                            match_confidence=95, availability='InStock', evidence_json={'condition':'NewCondition'})
        session.add(web)
        session.commit()
        wid = web.id
    assert winner().id == oid
    client.post(url, data=data | {'action':'revoke'})
    assert winner().id == wid
    client.post(url, data=data | {'vat':'excluded'})
    assert winner() is None
    client.post(url, data=data)
    with routes.SessionLocal() as session:
        supplier = session.get(Supplier, 1)
        supplier.active = False
        session.commit()
    assert winner() is None
    with routes.SessionLocal() as session:
        item = session.get(PurchaseRequestItem, 1)
        item.quantity = Decimal('3')
        session.commit()
    assert winner() is None
    assert client.post('/requests/999/offers/'+str(oid)+'/document-review', data=data).status_code == 404
    with routes.SessionLocal() as session:
        offer = session.get(SupplierOffer, oid)
        assert offer.match_confidence == 0 and offer.evidence_json['restricted'] is True
        assert len(offer.review_history_json) == 11


def test_batch_review_is_scoped_atomic_and_audited(client):
    save(client)
    with routes.SessionLocal() as session:
        item=session.get(PurchaseRequestItem,1)
        item.quantity=Decimal('2'); item.unit='unidade'
        supplier=Supplier(name='Lote',active=True)
        session.add(supplier); session.flush()
        offers=[SupplierOffer(purchase_request_item_id=1,supplier_id=supplier.id,product_name='PC',
                unit_price=Decimal('90'),currency='AOA',match_confidence=0,
                document_line_key=f'{i}:0',evidence_json={'import_id':i,'restricted':True}) for i in (1,2)]
        session.add_all(offers); session.commit()
        first,second=[o.id for o in offers]
    url='/requests/1/proformas/1/review-offers'
    data=dict(offer_ids=[first,second],reviewer='Ana', quantity_confirmed='yes', match='yes',unit='yes',
              stock='in',condition='new',conditions='yes',vat='included',
              valid_until=(utc_now().date()+timedelta(days=1)).isoformat())
    assert client.post(url,data=data).status_code==404
    with routes.SessionLocal() as session:
        assert not session.get(SupplierOffer,first).review_history_json
    data['offer_ids']=[first]
    assert client.post(url,data=data).status_code==200
    with routes.SessionLocal() as session:
        offer=session.get(SupplierOffer,first)
        assert len(offer.review_history_json)==1
        assert offer.review_history_json[0]['scope']=='batch'
        assert offer.review_history_json[0]['covered_quantity']=='2.000'
        item=session.get(PurchaseRequestItem,1)
        assert routes.load_results(session,[item])[1]['winner'].id==first
        assert not session.get(SupplierOffer,second).review_history_json
    assert client.post(url,data=data | {'offer_ids':[]}).status_code==422
    assert client.post(url,data=data | {'quantity_confirmed':'no'}).status_code==422
