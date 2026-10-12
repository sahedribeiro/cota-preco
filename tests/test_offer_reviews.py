from datetime import timedelta
from sqlalchemy import select

from app.models import PurchaseRequest, Supplier, SupplierOffer, PriceSearch, utc_now
from tests.test_requests import client
from tests.test_prices import item, product, html
import app.routes.requests as routes
import app.services.price_search_service as search_module


def test_review_comparison_audit_revocation_and_refresh(client, monkeypatch):
    data = product()
    data['offers']['priceSpecification'] = {}
    monkeypatch.setattr(search_module, 'read_allowed_html', lambda url: html(data))
    with routes.SessionLocal() as session:
        purchase = PurchaseRequest(title='Revisão')
        supplier = Supplier(name='Teste', website='https://example.com/', active=True, scraping_enabled=True)
        session.add_all([purchase, supplier]); session.flush()
        requested = item(); requested.purchase_request_id = purchase.id
        session.add(requested); session.commit()
        search_url = f'/requests/{purchase.id}/items/{requested.id}/search'
        request_id, supplier_id = purchase.id, supplier.id
    client.post(search_url, data={'supplier_ids': supplier_id})
    with routes.SessionLocal() as session:
        offer = session.scalar(select(SupplierOffer))
        offer_id = offer.id
        original = dict(offer.evidence_json)
    url = f'/requests/{request_id}/offers/{offer_id}/review'
    review = dict(reviewer='Ana <script>', notes='Documento confirmado', match='yes', unit='yes', vat='included', condition='new', stock='in')
    assert client.post(url, data={**review, 'reviewer':'  '}).status_code == 422
    assert client.post(url, data={**review, 'vat':'maybe'}).status_code == 422
    assert client.post(f'/requests/999/offers/{offer_id}/review', data=review).status_code == 404
    response = client.post(url, data=review)
    assert response.status_code == 200
    assert 'Menor preço comparável</strong>' in response.text
    assert 'Ana &lt;script&gt;' in response.text
    with routes.SessionLocal() as session:
        saved = session.get(SupplierOffer, offer_id)
        assert saved.evidence_json == original and saved.vat_included is None
        assert saved.match_confidence == 74
        assert len(saved.review_history_json) == 1
        assert saved.review_history_json[0]['reviewed_at']
    rejected = client.post(url, data={**review, 'match':'no'})
    assert 'Menor preço comparável</strong>' not in rejected.text
    revoked = client.post(url, data={**review, 'action':'revoke'})
    assert 'Menor preço comparável</strong>' not in revoked.text
    assert 'Revisão revogada' in revoked.text
    client.post(url, data=review)
    refreshed = client.post(search_url, data={'supplier_ids':supplier_id})
    assert 'Menor preço comparável</strong>' not in refreshed.text
    assert client.post(url, data=review).status_code == 409
    with routes.SessionLocal() as session:
        assert len(session.get(SupplierOffer, offer_id).review_history_json) == 4
        latest = session.scalar(select(SupplierOffer).order_by(SupplierOffer.id.desc()))
        assert latest.review_history_json == []
        search = session.get(PriceSearch, latest.price_search_id)
        search.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
        latest_id = latest.id
    assert client.post(f'/requests/{request_id}/offers/{latest_id}/review', data=review).status_code == 409
