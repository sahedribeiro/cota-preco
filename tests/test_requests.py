from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.core.config import settings, BASE_DIR
from app.core.database import build_engine
from app.models import PurchaseRequest, PurchaseRequestItem
import app.routes.requests as routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'database_url', f"sqlite:///{(tmp_path / 'requests.db').as_posix()}")
    monkeypatch.setattr(settings, 'upload_path', tmp_path / 'uploads')
    command.upgrade(Config(str(BASE_DIR / 'alembic.ini')), 'head')
    engine = build_engine(settings.database_url)
    monkeypatch.setattr(routes, 'SessionLocal', sessionmaker(bind=engine, expire_on_commit=False))
    with TestClient(app) as client:
        yield client
    engine.dispose()


def save(client):
    import json
    draft = {'title':'Computadores <script>', 'conditions':['Considerar IVA'], 'items':[{
        'description':'PC', 'normalized_description':'Computador i7', 'brand':None,
        'model':None, 'reference':None, 'quantity':None, 'unit':None,
        'specifications':[{'name':'RAM','value':'16 GB'}]}]}
    return client.post('/requests', data={'draft_json':json.dumps(draft)}, follow_redirects=False)


def test_save_list_detail_edit_and_reload(client):
    response = save(client)
    assert response.status_code == 303
    url = response.headers['location']
    listing = client.get('/requests')
    assert 'Computadores &lt;script&gt;' in listing.text
    detail = client.get(url)
    assert detail.status_code == 200
    assert 'Por indicar' in detail.text
    assert 'Não pesquisado' in detail.text
    with routes.SessionLocal() as session:
        item = session.scalars(select(PurchaseRequestItem)).one()
        item_id = item.id
        assert item.quantity is None and item.unit is None
        assert session.scalars(select(PurchaseRequest)).one().description == 'Considerar IVA'
    response = client.post(f'{url}/items/{item_id}', data={'description':'PC actualizado', 'quantity':'2,5', 'unit':'unidade', 'specifications':'RAM: 32 GB\nSSD: 512 GB'})
    assert response.status_code == 200
    assert 'PC actualizado' in response.text
    with routes.SessionLocal() as session:
        item = session.get(PurchaseRequestItem, item_id)
        assert item.quantity == Decimal('2.500')
        assert item.specifications_json == {'RAM':'32 GB', 'SSD':'512 GB'}
    assert client.post(url, data={'title':'Pedido revisto','description':'Entrega em Luanda'}).status_code == 200
    assert 'Pedido revisto' in client.get('/requests').text


@pytest.mark.parametrize('quantity', ['0','-1','NaN','Infinity','1.0001','1000000000'])
def test_invalid_quantity_keeps_original(client, quantity):
    save(client)
    response = client.post('/requests/1/items/1', data={'description':'PC','quantity':quantity})
    assert response.status_code == 422
    with routes.SessionLocal() as session:
        assert session.get(PurchaseRequestItem, 1).quantity is None


def test_cross_request_item_and_missing_request(client):
    save(client)
    save(client)
    assert client.post('/requests/2/items/1', data={'description':'Wrong'}).status_code == 404
    assert client.get('/requests/999').status_code == 404


def test_invalid_draft_and_specs(client):
    assert client.post('/requests', data={'draft_json':'{}'}).status_code == 422
    save(client)
    assert client.post('/requests/1/items/1', data={'description':'PC','specifications':'RAM 16GB'}).status_code == 422
    assert client.post('/requests/1', data={'title':'   '}).status_code == 422


def test_add_and_remove_unresearched_item(client):
    save(client)
    response = client.post('/requests/1/items', data={'description':'Papel', 'quantity':'2,5', 'unit':'resma', 'specifications':'Formato: A4'})
    assert response.status_code == 200
    with routes.SessionLocal() as session:
        item = session.scalar(select(PurchaseRequestItem).where(PurchaseRequestItem.description == 'Papel'))
        assert item.quantity == Decimal('2.500') and item.specifications_json == {'Formato':'A4'}
        item_id = item.id
    assert client.post(f'/requests/999/items/{item_id}/remove').status_code == 404
    assert client.post(f'/requests/1/items/{item_id}/remove').status_code == 200
    with routes.SessionLocal() as session:
        assert session.get(PurchaseRequestItem, item_id) is None
    assert client.post('/requests/1/items', data={'description':' ', 'quantity':'1'}).status_code == 422
    assert client.post('/requests/1/items', data={'description':'Papel', 'quantity':'NaN'}).status_code == 422


def test_removal_preserves_search_and_document_history(client):
    from datetime import timedelta
    from app.models import PriceSearch, Supplier, SupplierOffer, utc_now
    save(client)
    save(client)
    with routes.SessionLocal() as session:
        session.add(PriceSearch(purchase_request_item_id=1, search_query='PC', expires_at=utc_now()+timedelta(hours=1)))
        supplier = Supplier(name='Teste'); session.add(supplier); session.flush()
        session.add(SupplierOffer(purchase_request_item_id=2, supplier_id=supplier.id, product_name='PC', document_line_key='test:0'))
        session.commit()
    assert client.post('/requests/1/items/1/remove').status_code == 409
    assert client.post('/requests/2/items/2/remove').status_code == 409
    assert client.post('/requests/1/items/2/remove').status_code == 404
    with routes.SessionLocal() as session:
        assert session.get(PurchaseRequestItem, 1) and session.get(PurchaseRequestItem, 2)
