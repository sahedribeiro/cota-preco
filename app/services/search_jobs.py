"""Small in-process runner for the single-worker MVP; no external queue."""
from copy import deepcopy
from threading import Lock, Thread
from collections.abc import Callable
import logging

from sqlalchemy import select
from app.models import PurchaseRequestItem, Supplier
from app.services.price_search_service import PriceSearchService

logger = logging.getLogger(__name__)


class SearchJobs:
    def __init__(self) -> None:
        self.lock = Lock()
        self.jobs: dict[int, dict] = {}
        self.busy = False

    def status(self, request_id: int) -> dict:
        with self.lock:
            return deepcopy(self.jobs.get(request_id, {'state':'idle'}))

    def cancel(self, request_id: int) -> dict:
        with self.lock:
            job = self.jobs.get(request_id)
            if job is None:
                raise ValueError('Não existe pesquisa nesta requisição.')
            if job['state'] == 'running':
                job['state'] = 'stopping'
            return deepcopy(job)

    def start(self, request_id: int, item_ids: list[int], supplier_ids: list[int], refresh: bool,
              session_factory: Callable) -> dict:
        with self.lock:
            if self.busy:
                raise ValueError('Já existe uma pesquisa em curso. Aguarda a conclusão antes de iniciar outra.')
            self.busy = True
            if len(self.jobs) >= 100:
                self.jobs.pop(next(iter(self.jobs)))
            self.jobs[request_id] = {'state':'running', 'total':len(item_ids), 'done':0, 'current':None, 'results':[]}
        try:
            Thread(target=self.run, args=(request_id, item_ids, supplier_ids, refresh, session_factory), daemon=True).start()
        except Exception:
            with self.lock:
                self.busy = False
                self.jobs[request_id]['state'] = 'failed'
            raise
        return self.status(request_id)

    def run(self, request_id: int, item_ids: list[int], supplier_ids: list[int], refresh: bool,
            session_factory: Callable) -> None:
        try:
            for item_id in item_ids:
                with self.lock:
                    if self.jobs[request_id]['state'] == 'stopping':
                        break
                    self.jobs[request_id]['current'] = item_id
                outcome = {'item_id':item_id, 'status':'failed', 'message':'Não foi possível concluir este artigo.'}
                try:
                    with session_factory() as session:
                        item = session.get(PurchaseRequestItem, item_id)
                        suppliers = list(session.scalars(select(Supplier).where(Supplier.id.in_(supplier_ids))))
                        if item is None or item.purchase_request_id != request_id or len(suppliers) != len(supplier_ids):
                            raise ValueError('Artigo ou fornecedores alterados durante a pesquisa.')
                        search = PriceSearchService().search(session, item, suppliers, refresh=refresh)
                        outcome.update(status=search.status, message='Consulta incompleta' if search.status == 'partial' else 'Consulta concluída')
                except Exception:
                    logger.exception('Pesquisa em segundo plano falhou para artigo %s', item_id)
                with self.lock:
                    job = self.jobs[request_id]
                    job['results'].append(outcome)
                    job['done'] += 1
        finally:
            with self.lock:
                job = self.jobs[request_id]
                job['current'] = None
                job['state'] = 'cancelled' if job['state'] == 'stopping' and job['done'] < job['total'] else 'completed' if all(r['status'] == 'completed' for r in job['results']) and job['done'] == job['total'] else 'partial'
                self.busy = False


search_jobs = SearchJobs()
