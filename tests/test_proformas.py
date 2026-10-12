from decimal import Decimal
from sqlalchemy import select, func

from app.main import app
from app.models import PurchaseRequest, PurchaseRequestItem, Supplier, SupplierOffer, ProformaImport, UsageRecord
from app.routes.web import ai_service
from app.schemas.proforma import ProformaDraft
from app.services.ai_service import AIResult
from tests.test_requests import client
import app.routes.requests as requests
import app.routes.proformas as routes


def test_proforma_review_save_and_no_duplicates(client, monkeypatch):
    monkeypatch.setattr(routes, 'SessionLocal', requests.SessionLocal)
    calls = []

    class FakeAI:
        def extract_comparison_block(self, text, filename, role, image=None):
            calls.append(text)
            return AIResult(ProformaDraft(
                supplier_name='Vendedor <script>', document_reference='PF-1', document_date='01/01/2020',
                valid_until=None, warnings='Documento antigo; confirmar condiÃ§Ãµes.', lines=[dict(
                    description='PC', reference=None, quantity='2', unit='unidade', unit_price='1500.25',
                    currency='AOA', vat_included=None, evidence='PC 2 x 1500,25')]), 'mock', 10, 20)

    with requests.SessionLocal() as session:
        purchase = PurchaseRequest(title='Teste')
        other = PurchaseRequest(title='Outra')
        supplier = Supplier(name='Vendedor')
        session.add_all([purchase, other, supplier]); session.flush()
        item = PurchaseRequestItem(purchase_request_id=purchase.id, description='PC', quantity=2, unit='unidade')
        wrong = PurchaseRequestItem(purchase_request_id=other.id, description='Outro')
        session.add_all([item, wrong]); session.commit()
        request_id, other_id, item_id, wrong_id, supplier_id = purchase.id, other.id, item.id, wrong.id, supplier.id
    app.dependency_overrides[ai_service] = FakeAI
    try:
        root = f'/requests/{request_id}/proformas'
        preview = client.post(root, files={'document':('pf.txt', b'PC 2 x 1500,25', 'text/plain')})
        assert preview.status_code == 200
        assert 'Vendedor &lt;script&gt;' in preview.text
        assert len(calls) == 1
        with requests.SessionLocal() as session:
            assert session.scalar(select(func.count()).select_from(SupplierOffer)) == 0
            record = session.scalar(select(ProformaImport))
            import_id = record.id
            assert session.scalar(select(UsageRecord)).output_tokens == 20
        url = f'{root}/{import_id}/lines/0'
        data = dict(item_id=item_id, supplier_id=supplier_id, product_name='PC corrigido', unit_price='1500.25',
                    currency='AOA', vat='unknown', reviewer='Ana', notes='PreÃ§o revisto no documento', confirmed='yes')
        for invalid in ['NaN', 'Infinity', '-1', '0', '1.001']:
            assert client.post(url, data={**data, 'unit_price':invalid}).status_code == 422
        assert client.post(url, data={**data, 'item_id':wrong_id}).status_code == 422
        assert client.post(url, data={**data, 'reviewer':' '}).status_code == 422
        saved = client.post(url, data=data)
        assert saved.status_code == 200 and '1.500,25 Kz' in saved.text
        assert client.post(url, data=data).status_code == 200
        assert len(calls) == 1
        with requests.SessionLocal() as session:
            offer = session.scalars(select(SupplierOffer)).one()
            assert offer.unit_price == Decimal('1500.25') and offer.vat_included is None
            assert offer.price_search_id is None and offer.match_confidence == 0
            assert offer.evidence_json['original_line']['description'] == 'PC'
        assert 'Ofertas de proformas' in client.get(f'/requests/{request_id}').text
        assert client.get(f'{root}/{import_id}/document').content == b'PC 2 x 1500,25'
        assert client.get(f'/requests/{other_id}/proformas/{import_id}/document').status_code == 404
        assert client.post(root, files={'document':('bad.exe', b'bad')}).status_code == 422
        assert len(calls) == 1
    finally:
        app.dependency_overrides.pop(ai_service, None)


def test_bulk_review_defaults_atomicity_and_idempotency(client, monkeypatch):
    monkeypatch.setattr(routes, 'SessionLocal', requests.SessionLocal)
    with requests.SessionLocal() as session:
        purchase = PurchaseRequest(title='Lote')
        supplier = Supplier(name='Loja A')
        session.add_all([purchase, supplier]); session.flush()
        item = PurchaseRequestItem(purchase_request_id=purchase.id, description='PC', quantity=2, unit='unidade')
        session.add(item); session.flush()
        record = ProformaImport(purchase_request_id=purchase.id, stored_filename='unused.txt', draft_json={
            'supplier_name':'Loja A', 'lines':[
                {'description':'PC', 'reference':None, 'currency':None, 'unit_price':'50.00'},
                {'description':'PC', 'reference':None, 'currency':'USD', 'unit_price':'60.00'},
                {'description':'Outro', 'reference':None, 'currency':None, 'unit_price':None}]})
        session.add(record); session.commit()
        root=f'/requests/{purchase.id}/proformas/{record.id}'
        iid, sid = item.id, supplier.id
    page=client.get(root)
    assert page.status_code == 200
    assert f'value="{sid}" selected' in page.text
    assert f'value="{iid}" selected' in page.text
    assert 'value="AOA"' in page.text and 'value="USD"' in page.text
    assert 'name="confirmed"' not in page.text
    data={'supplier_id':sid,'reviewer':'Ana'}
    for index in (0,1):
        data.update({f'line_{index}_{key}':value for key,value in dict(item_id=iid,product_name='PC',unit_price='50.00',currency='AOA',vat='unknown').items()})
    bad=data | {'line_1_unit_price':'-1'}
    assert client.post(root+'/save', data=bad).status_code == 422
    with requests.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(SupplierOffer)) == 0
    assert client.post(root+'/save', data=data).status_code == 200
    assert client.post(root+'/save', data=data).status_code == 200
    with requests.SessionLocal() as session:
        offers=session.scalars(select(SupplierOffer)).all()
        assert len(offers)==2
        assert all(o.evidence_json['notes']=='' and o.vat_included is None for o in offers)
        assert all(o.evidence_json['restricted'] for o in offers)
    page=client.get(root)
    assert 'value="Ana"' in page.text
    assert client.post('/requests/999/proformas/1/save',data=data).status_code==404
