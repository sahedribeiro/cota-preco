from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import pymupdf
from sqlalchemy import select, func

from app.models import PriceRecord, CatalogImport, PurchaseRequest
from app.services.comparison_documents import extract_document, blank_document
from app.services.procurement_matrix import store_source, prepare_source
from app.routes import catalog, proformas, requests
from app.routes.web import ai_service
from app.main import app
from tests.test_requests import client


def test_mixed_pdf_keeps_scanned_pages_and_conflicting_headers(tmp_path):
    path = tmp_path / 'mixed.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 50), 'Fornecedor A - produto 1')
        pdf.new_page()
        pdf.save(path)
    calls = []
    class Fake:
        def extract_comparison_block(self, text, filename, role, image):
            calls.append(image)
            draft = blank_document()
            draft.update(supplier_name='Loja B' if image else 'Loja A', warnings='Aviso da pagina',
                         lines=[dict(description='Artigo '+str(len(calls)), unit_price='10', currency='AOA', confidence=99)])
            return SimpleNamespace(response=SimpleNamespace(model_dump=lambda: draft))
    result = extract_document(path, path.name, 'supplier', Fake(), lambda _: None)
    assert len(result['lines']) == 2 and calls[0] is None and calls[1].startswith(b'\x89PNG')
    assert result['conflict'] and all(line['confidence'] == 0 for line in result['lines'])
    assert 'Dados divergentes' in result['warnings']
    assert extract_document(path, path.name, 'supplier', Fake(), lambda _: None) == result
    assert len(calls) == 2


def test_large_csv_request_keeps_every_line_and_ignores_prices(tmp_path):
    path = tmp_path / 'request.csv'
    path.write_text('Descricao;Quantidade;Preco\n' + '\n'.join(f'Produto {i};2;999' for i in range(250)), encoding='utf-8')
    result = extract_document(path, path.name, 'request', None, lambda _: None)
    assert len(result['lines']) == 250
    assert all(line['unit_price'] is None for line in result['lines'])


@pytest.mark.skipif(not (Path(__file__).parent / 'fixtures/girassol/neomed.pdf').is_file(), reason='Proforma privada disponível apenas localmente')
def test_real_proforma_shared_between_catalog_and_legacy_without_ai(client, monkeypatch):
    monkeypatch.setattr(catalog, 'SessionLocal', requests.SessionLocal)
    monkeypatch.setattr(proformas, 'SessionLocal', requests.SessionLocal)
    class NoAI:
        def extract_comparison_block(self, *args):
            raise AssertionError('Supported real fixture needs no paid API')
    app.dependency_overrides[ai_service] = NoAI
    try:
        payload = (Path(__file__).parent / 'fixtures/girassol/neomed.pdf').read_bytes()
        with requests.SessionLocal() as session:
            purchase = PurchaseRequest(title='Teste partilha');session.add(purchase);session.commit();pid=purchase.id
        response = client.post('/catalog/import', files={'document': ('neomed.pdf', payload)}, follow_redirects=False)
        url = response.headers['location']
        assert client.post(url+'/extract').status_code == 200
        assert client.post(url+'/save', data={'new_supplier':'NEOMED'}).status_code == 200
        assert client.post(f'/requests/{pid}/proformas', files={'document': ('neomed.pdf', payload)}).status_code == 200
        with requests.SessionLocal() as session:
            assert session.scalar(select(func.count()).select_from(PriceRecord)) == 37
    finally:
        app.dependency_overrides.pop(ai_service, None)


def test_same_reference_changed_prices_preserves_history_and_requires_review(client):
    draft = dict(blank_document(), supplier_name='Fornecedor', document_reference='PF-123', file_hash='a', filename='a.pdf',
                 lines=[dict(description='PC', unit='un', unit_price='100', currency='AOA', confidence=100)])
    with requests.SessionLocal() as session:
        store_source(session, prepare_source(deepcopy(draft)))
        session.commit()
        revised = deepcopy(draft);revised['lines'][0]['unit_price']='120'
        store_source(session, prepare_source(revised));session.commit()
        store_source(session, prepare_source(deepcopy(revised)));session.commit()
        records = list(session.scalars(select(PriceRecord).order_by(PriceRecord.id)))
        assert len(records) == 2
        assert str(records[0].unit_price) == '100.00' and records[0].status == 'accepted'
        assert str(records[1].unit_price) == '120.00' and records[1].status == 'review'
        assert revised['lines'][0]['confidence'] == 0
