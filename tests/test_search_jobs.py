import pytest
from threading import Event
from types import SimpleNamespace

from app.models import PurchaseRequest, PurchaseRequestItem, Supplier
from app.services.search_jobs import SearchJobs
import app.services.search_jobs as jobs_module
import app.routes.requests as routes
from tests.test_requests import client


@pytest.mark.parametrize('cancel', [False, True])
def test_background_search_nonblocking_progress_scope_and_failures(client, monkeypatch, cancel):
    jobs = SearchJobs()
    monkeypatch.setattr(jobs_module, 'search_jobs', jobs)
    entered, release, finished = Event(), Event(), Event()
    calls = []
    def fake_search(self, session, item, suppliers, refresh=False):
        calls.append((item.id, refresh))
        entered.set()
        assert release.wait(5)
        if len(calls) == 2:
            raise ValueError('Simulated supplier failure')
        return SimpleNamespace(status='completed')
    monkeypatch.setattr(jobs_module.PriceSearchService, 'search', fake_search)
    original = jobs.run
    def run(*args):
        try: original(*args)
        finally: finished.set()
    monkeypatch.setattr(jobs, 'run', run)
    with routes.SessionLocal() as session:
        purchase=PurchaseRequest(title='Pesquisa')
        other=PurchaseRequest(title='Outra')
        supplier=Supplier(name='Loja',website='https://example.com',active=True,scraping_enabled=True)
        session.add_all([purchase,other,supplier]); session.flush()
        items=[PurchaseRequestItem(purchase_request_id=purchase.id,description=f'PC {i}') for i in range(2)]
        wrong=PurchaseRequestItem(purchase_request_id=other.id,description='Outro')
        session.add_all([*items,wrong]); session.commit()
        ids=[i.id for i in items]; sid=supplier.id; wid=wrong.id
        url=f'/requests/{purchase.id}/search-jobs'
    assert client.post(url,data={'item_ids':[wid],'supplier_ids':[sid]}).status_code==422
    assert client.post(url,data={'item_ids':ids,'supplier_ids':[sid],'refresh':'true'}).status_code==202
    try:
        assert entered.wait(2)
        status=client.get(url)
        assert status.json()['state']=='running' and status.json()['done']==0
        assert status.headers['cache-control']=='no-store'
        assert client.get('/health').status_code==200
        assert client.post(url,data={'item_ids':ids,'supplier_ids':[sid]}).status_code==409
        if cancel:
            assert client.post('/requests/2/search-jobs/cancel').status_code == 404
            assert client.post(url+'/cancel').json()['state'] == 'stopping'
            assert client.post(url+'/cancel').json()['state'] == 'stopping'
            assert client.post(url,data={'item_ids':ids,'supplier_ids':[sid]}).status_code==409
    finally:
        release.set()
        assert finished.wait(5)
    result=client.get(url).json()
    if cancel:
        assert result['state']=='cancelled' and result['done']==1
        assert calls==[(ids[0],True)] and not jobs.busy
        assert result['results'][0]['status']=='completed'
        assert client.post(url+'/cancel').json()['state']=='cancelled'
        return
    assert result['state']=='partial' and result['done']==2
    assert [r['status'] for r in result['results']]==['completed','failed']
    assert calls==[(ids[0],True),(ids[1],True)]
    assert client.get('/requests/999/search-jobs').status_code==404
    assert client.get('/requests/1').status_code==200
