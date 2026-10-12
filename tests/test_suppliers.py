import pytest
from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config
from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker
from app.core.config import settings, BASE_DIR
from app.core.database import build_engine
from app.main import app
from app.models import Supplier, UsageRecord
from app.routes.web import ai_service
from app.schemas.supplier import SupplierDraft
from app.services.ai_service import AIResult
import app.routes.suppliers as routes
import app.seed_suppliers as seed


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'database_url', f"sqlite:///{(tmp_path / 'suppliers.db').as_posix()}")
    monkeypatch.setattr(settings, 'upload_path', tmp_path / 'uploads')
    command.upgrade(Config(str(BASE_DIR / 'alembic.ini')), 'head')
    engine = build_engine(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(routes, 'SessionLocal', factory)
    monkeypatch.setattr(seed, 'SessionLocal', factory)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    engine.dispose()


def form(**changes):
    data = dict(name='Fornecedor teste', website='', country='Angola', category='Informática', email='',phone='',tax_id='',notes='',source_kind='manual',active='true',reviewed='false',scraping_enabled='false')
    data.update(changes)
    return data


def test_seed_idempotent(client):
    assert seed.seed_suppliers() == 18
    assert seed.seed_suppliers() == 0
    assert 'Stylus Angola' in client.get('/suppliers').text
    assert 'Neomedic Angola' in client.get('/suppliers').text
    assert 'Stylus Angola' not in client.get('/suppliers?q=Neomedic').text
    with routes.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Supplier)) == 18
        assert not any(s.reviewed or s.scraping_enabled for s in session.scalars(select(Supplier)))


def test_create_edit_duplicate_deactivate(client):
    response = client.post('/suppliers/save', data=form(), follow_redirects=False)
    assert response.status_code == 303, response.text
    assert client.post('/suppliers/save', data=form()).status_code == 409
    assert client.post('/suppliers/save?supplier_id=1', data=form(name='Novo nome',active='false')).status_code == 200
    assert 'Novo nome' not in client.get('/suppliers').text
    assert 'Novo nome' in client.get('/suppliers?state=inactive').text
    assert client.post('/suppliers/save?supplier_id=999', data=form()).status_code == 404
    assert client.post('/suppliers/save', data=form(website='javascript:alert(1)')).status_code == 422


def test_import_document_requires_review(client):
    class FakeAI:
        def extract_supplier(self, text, source_kind):
            assert source_kind == 'proforma'
            assert 'Vendedor: Teste' in text
            return AIResult(SupplierDraft(name='Teste',website=None,country=None,category=None,email=None,phone=None,tax_id=None,notes='Confirmar identidade'), 'test', 20, 10)
    app.dependency_overrides[ai_service] = FakeAI
    response = client.post('/suppliers/import', data={'source_kind':'proforma'}, files={'document':('pedido.txt',b'Vendedor: Teste; Cliente: Outro')})
    assert response.status_code == 200
    assert 'Proposta extraída' in response.text
    with routes.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Supplier)) == 0
        assert session.scalar(select(func.count()).select_from(UsageRecord)) == 1


def test_disabled_site_does_not_fetch(client, monkeypatch):
    client.post('/suppliers/save', data=form(website='https://example.com/'))
    monkeypatch.setattr(routes, 'extract_website', lambda url: pytest.fail('Disabled site fetched'))
    response = client.post('/suppliers/import', data={'source_kind':'website','url':'https://example.com/'})
    assert response.status_code == 422
    assert 'desactivada' in response.text
