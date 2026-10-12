from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import PurchaseRequest, PurchaseRequestItem, Supplier, SupplierOffer, PriceSearch, utc_now
from app.services.price_search_service import PriceSearchService, cache_key, as_utc
from app.services.comparison_service import cheapest_per_item, comparison_values
from app.schemas.offer_review import OfferReviewInput, DocumentOfferReviewInput, BatchDocumentReviewInput
from app.services.document_offer_review import document_eligibility
from app.services.price_search_service import product_key
from app.routes.web import templates
from app.schemas.chat import PurchaseDraft
from app.schemas.request_item import RequestItemInput
from sqlalchemy.exc import IntegrityError
from app.services.purchase_summary import purchase_summary
from app.services.purchase_export import export_purchase

router = APIRouter()


def get_request(session: Session, request_id: int) -> PurchaseRequest:
    purchase = session.get(PurchaseRequest, request_id)
    if purchase is None:
        raise HTTPException(404, 'Requisição não encontrada.')
    return purchase


@router.get('/requests', response_class=HTMLResponse)
def list_requests(request: Request) -> HTMLResponse:
    with SessionLocal() as session:
        purchases = session.scalars(select(PurchaseRequest).order_by(PurchaseRequest.id.desc()).limit(100)).all()
        return templates.TemplateResponse(request=request, name='requests.html', context={'purchases': purchases})


@router.post('/requests')
def save_request(draft_json: Annotated[str, Form(max_length=50000)]) -> RedirectResponse:
    try:
        draft = PurchaseDraft.model_validate_json(draft_json)
    except ValidationError:
        raise HTTPException(422, 'Rascunho inválido. Volta ao chat e revê os artigos.') from None
    for item in draft.items:
        if item.quantity is not None:
            amount = Decimal(str(item.quantity))
            if not amount.is_finite() or amount != amount.quantize(Decimal('.001')):
                raise HTTPException(422, 'A quantidade deve ter até 3 casas decimais.')
        names = [spec.name for spec in item.specifications]
        if len(names) != len(set(names)):
            raise HTTPException(422, 'O artigo contém especificações repetidas. Revê o rascunho.')
    with SessionLocal() as session:
        purchase = PurchaseRequest(title=draft.title, description='\n'.join(draft.conditions), status='draft')
        session.add(purchase)
        session.flush()
        for item in draft.items:
            session.add(PurchaseRequestItem(
                purchase_request_id=purchase.id, description=item.description,
                normalized_description=item.normalized_description, brand=item.brand,
                model=item.model, reference=item.reference,
                quantity=Decimal(str(item.quantity)) if item.quantity is not None else None,
                unit=item.unit, specifications_json={spec.name: spec.value for spec in item.specifications},
            ))
        session.commit()
        return RedirectResponse(f'/requests/{purchase.id}', status_code=303)


def load_results(session: Session, items: list[PurchaseRequestItem]) -> dict:
    results = {}
    for item in items:
        search = session.scalar(select(PriceSearch).where(PriceSearch.purchase_request_item_id == item.id).order_by(PriceSearch.id.desc()))
        documents = list(session.scalars(select(SupplierOffer).where(
            SupplierOffer.purchase_request_item_id == item.id, SupplierOffer.document_line_key.is_not(None))))
        if (not search or not search.cache_key) and not documents:
            continue
        sources = list(session.scalars(select(Supplier).where(Supplier.id.in_(search.raw_result_json.get('supplier_ids', []))))) if search else []
        stale = not search or not search.cache_key or search.cache_key != cache_key(item, sources) or as_utc(search.expires_at) <= utc_now()
        offers = list(session.scalars(select(SupplierOffer).where(SupplierOffer.price_search_id == search.id))) if search else []
        names = {source.id:source.name for source in sources}
        document_status = {}
        for offer in documents:
            supplier = session.get(Supplier, offer.supplier_id)
            names[supplier.id] = supplier.name
            document_status[offer.id] = document_eligibility(offer, item, supplier)
        approved = {offer_id for offer_id, (valid, _) in document_status.items() if valid}
        candidates = ([] if stale else offers) + [offer for offer in documents if offer.id in approved]
        winner, reason = cheapest_per_item(candidates, approved)
        if stale and search:
            reason = 'Pesquisa web desactualizada; apenas proformas válidas podem concorrer. ' + reason
        if search and search.status == 'partial':
            reason = 'Comparação parcial: uma ou mais fontes falharam. Considera apenas as ofertas recolhidas. ' + reason
        results[item.id] = {'search':search, 'offers':offers, 'winner':winner, 'reason':reason, 'stale':stale,
                            'comparison_stale': False, 'document_status':document_status, 'names':names,
                            'values':{offer.id: comparison_values(offer) for offer in offers}}
    return results


@router.get('/requests/{request_id}', response_class=HTMLResponse)
def detail(request: Request, request_id: int) -> HTMLResponse:
    with SessionLocal() as session:
        purchase = get_request(session, request_id)
        items = session.scalars(select(PurchaseRequestItem).where(PurchaseRequestItem.purchase_request_id == request_id).order_by(PurchaseRequestItem.id)).all()
        suppliers = session.scalars(select(Supplier).where(Supplier.active.is_(True), Supplier.scraping_enabled.is_(True), Supplier.website.is_not(None)).order_by(Supplier.name)).all()
        results = load_results(session, items)
        current_ids = {offer.id for result in results.values() for offer in result['offers']}
        document_offers = session.scalars(select(SupplierOffer).where(
            SupplierOffer.purchase_request_item_id.in_([item.id for item in items]),
            SupplierOffer.document_line_key.is_not(None)).order_by(SupplierOffer.id.desc())).all()
        document_supplier_names = {offer.supplier_id: session.get(Supplier, offer.supplier_id).name for offer in document_offers}
        previous_reviews = [offer for offer in session.scalars(select(SupplierOffer).where(
            SupplierOffer.purchase_request_item_id.in_([item.id for item in items])
        ).order_by(SupplierOffer.id.desc())) if offer.review_history_json and not offer.document_line_key and offer.id not in current_ids]
        item_ids = [item.id for item in items]
        locked_ids = set(session.scalars(select(PriceSearch.purchase_request_item_id).where(PriceSearch.purchase_request_item_id.in_(item_ids))))
        locked_ids.update(session.scalars(select(SupplierOffer.purchase_request_item_id).where(SupplierOffer.purchase_request_item_id.in_(item_ids))))
        return templates.TemplateResponse(request=request, name='request_detail.html', context={
            'purchase': purchase, 'items': items, 'suppliers':suppliers, 'results':results,
            'previous_reviews': previous_reviews, 'items_by_id': {item.id:item for item in items},
            'document_offers': document_offers, 'document_supplier_names': document_supplier_names,
            'summary': purchase_summary(items, results),
            'removable_ids': set(item_ids) - locked_ids,
        })


@router.get('/requests/{request_id}/export.xlsx')
def download_summary(request_id: int) -> Response:
    with SessionLocal() as session:
        purchase = get_request(session, request_id)
        items = list(session.scalars(select(PurchaseRequestItem).where(PurchaseRequestItem.purchase_request_id == request_id).order_by(PurchaseRequestItem.id)))
        summary = purchase_summary(items, load_results(session, items))
        documents = list(session.scalars(select(SupplierOffer).where(
            SupplierOffer.purchase_request_item_id.in_([item.id for item in items]),
            SupplierOffer.document_line_key.is_not(None)).order_by(SupplierOffer.id)))
        names = {offer.supplier_id: session.get(Supplier, offer.supplier_id).name for offer in documents}
        content = export_purchase(purchase, summary, documents, names)
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="requisicao-{request_id}.xlsx"',
                             'Cache-Control': 'no-store'})


@router.post('/requests/{request_id}')
def edit_request(request_id: int, title: Annotated[str, Form(min_length=1, max_length=200)], description: Annotated[str, Form(max_length=5000)] = '') -> RedirectResponse:
    if not title.strip():
        raise HTTPException(422, 'O título não pode ficar vazio. Volta atrás para corrigir.')
    with SessionLocal() as session:
        purchase = get_request(session, request_id)
        purchase.title = title.strip()
        purchase.description = description.strip() or None
        session.commit()
    return RedirectResponse(f'/requests/{request_id}', status_code=303)


@router.post('/requests/{request_id}/items')
def add_item(request_id: int, data: Annotated[RequestItemInput, Form()]) -> RedirectResponse:
    with SessionLocal() as session:
        get_request(session, request_id)
        item = PurchaseRequestItem(purchase_request_id=request_id, **data.item_values())
        session.add(item)
        session.commit()
        return RedirectResponse(f'/requests/{request_id}#item-{item.id}', status_code=303)


@router.post('/requests/{request_id}/items/{item_id}')
def edit_item(request_id: int, item_id: int, data: Annotated[RequestItemInput, Form()]) -> RedirectResponse:
    with SessionLocal() as session:
        get_request(session, request_id)
        item = session.get(PurchaseRequestItem, item_id)
        if item is None or item.purchase_request_id != request_id:
            raise HTTPException(404, 'Artigo não encontrado nesta requisição.')
        for field, value in data.item_values().items():
            setattr(item, field, value)
        session.commit()
    return RedirectResponse(f'/requests/{request_id}#item-{item_id}', status_code=303)


@router.post('/requests/{request_id}/items/{item_id}/remove')
def remove_item(request_id: int, item_id: int) -> RedirectResponse:
    with SessionLocal() as session:
        get_request(session, request_id)
        item = session.get(PurchaseRequestItem, item_id)
        if item is None or item.purchase_request_id != request_id:
            raise HTTPException(404, 'Artigo não encontrado nesta requisição.')
        has_search = select(PriceSearch.id).where(PriceSearch.purchase_request_item_id == item_id).exists()
        has_offer = select(SupplierOffer.id).where(SupplierOffer.purchase_request_item_id == item_id).exists()
        try:
            removed = session.execute(delete(PurchaseRequestItem).where(
                PurchaseRequestItem.id == item_id, ~has_search, ~has_offer))
            if removed.rowcount != 1:
                raise HTTPException(409, 'Este artigo tem pesquisas ou ofertas. O histórico foi preservado.')
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, 'O artigo recebeu dados associados e não pode ser removido.') from None
    return RedirectResponse(f'/requests/{request_id}', status_code=303)


@router.post('/requests/{request_id}/items/{item_id}/search')
def search_prices(request_id: int, item_id: int,
                  supplier_ids: Annotated[list[int], Form()],
                  refresh: Annotated[bool, Form()] = False) -> RedirectResponse:
    with SessionLocal() as session:
        get_request(session, request_id)
        item = session.get(PurchaseRequestItem, item_id)
        if item is None or item.purchase_request_id != request_id:
            raise HTTPException(404, 'Artigo não encontrado.')
        ids = set(supplier_ids)
        if not 1 <= len(ids) <= 5:
            raise HTTPException(422, 'Selecciona entre 1 e 5 fornecedores.')
        suppliers = session.scalars(select(Supplier).where(Supplier.id.in_(ids))).all()
        if len(suppliers) != len(ids):
            raise HTTPException(422, 'Fornecedor não encontrado.')
        try:
            PriceSearchService().search(session, item, suppliers, refresh=refresh)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
    return RedirectResponse(f'/requests/{request_id}#prices-{item_id}', status_code=303)


@router.post('/requests/{request_id}/offers/{offer_id}/review')
def review_offer(request_id: int, offer_id: int, data: Annotated[OfferReviewInput, Form()]) -> RedirectResponse:
    with SessionLocal() as session:
        offer = session.get(SupplierOffer, offer_id)
        item = session.get(PurchaseRequestItem, offer.purchase_request_item_id) if offer else None
        if item is None or item.purchase_request_id != request_id:
            raise HTTPException(404, 'Oferta não encontrada nesta requisição.')
        search = session.get(PriceSearch, offer.price_search_id) if offer.price_search_id else None
        latest = session.scalar(select(PriceSearch.id).where(PriceSearch.purchase_request_item_id == item.id).order_by(PriceSearch.id.desc()))
        sources = session.scalars(select(Supplier).where(Supplier.id.in_(search.raw_result_json.get('supplier_ids', [])))).all() if search else []
        if not search or latest != search.id or search.status not in {'completed', 'partial'} or as_utc(search.expires_at) <= utc_now() or search.cache_key != cache_key(item, sources):
            raise HTTPException(409, 'A oferta está desactualizada. Actualiza a pesquisa antes de rever.')
        if data.action == 'save' and data.unit == 'yes' and not item.unit:
            raise HTTPException(422, 'Indica primeiro a unidade no artigo da requisição.')
        entry = data.model_dump()
        entry['reviewed_at'] = utc_now().isoformat()
        offer.review_history_json = [*(offer.review_history_json or []), entry]
        session.commit()
        return RedirectResponse(f'/requests/{request_id}#offer-{offer.id}', status_code=303)


@router.post('/requests/{request_id}/offers/{offer_id}/document-review')
def review_document_offer(request_id: int, offer_id: int, data: Annotated[DocumentOfferReviewInput, Form()]) -> RedirectResponse:
    with SessionLocal() as session:
        offer = session.get(SupplierOffer, offer_id)
        item = session.get(PurchaseRequestItem, offer.purchase_request_item_id) if offer else None
        if item is None or item.purchase_request_id != request_id or not offer.document_line_key:
            raise HTTPException(404, 'Oferta documental não encontrada nesta requisição.')
        if data.action == 'save' and (item.quantity is None or not item.unit):
            raise HTTPException(422, 'Preenche a quantidade e unidade do artigo antes de validar a proforma.')
        entry = data.model_dump(mode='json')
        entry.update(reviewed_at=utc_now().isoformat(), item_key=product_key(item), requested_quantity=str(item.quantity))
        offer.review_history_json = [*(offer.review_history_json or []), entry]
        session.commit()
        return RedirectResponse(f'/requests/{request_id}#document-offer-{offer.id}', status_code=303)


@router.post('/requests/{request_id}/proformas/{import_id}/review-offers')
def review_document_batch(request_id: int, import_id: int,
                          data: Annotated[BatchDocumentReviewInput, Form()]) -> RedirectResponse:
    with SessionLocal() as session:
        get_request(session, request_id)
        for offer_id in set(data.offer_ids):
            offer = session.get(SupplierOffer, offer_id)
            item = session.get(PurchaseRequestItem, offer.purchase_request_item_id) if offer else None
            if item is None or item.purchase_request_id != request_id or not offer.document_line_key or offer.evidence_json.get('import_id') != import_id:
                raise HTTPException(404, 'Oferta não pertence a esta proforma e requisição.')
            if data.action == 'save' and (item.quantity is None or not item.unit):
                raise HTTPException(422, f'Artigo #{item.id}: preenche quantidade e unidade. Nenhuma revisão foi guardada.')
            entry = data.model_dump(mode='json', exclude={'offer_ids', 'quantity_confirmed'})
            entry.update(covered_quantity=str(item.quantity), reviewed_at=utc_now().isoformat(),
                         item_key=product_key(item), requested_quantity=str(item.quantity), scope='batch')
            offer.review_history_json = [*(offer.review_history_json or []), entry]
        session.commit()
    return RedirectResponse(f'/requests/{request_id}#document-offers', status_code=303)


@router.post('/requests/{request_id}/search-jobs', status_code=202)
def start_search_job(request_id: int, item_ids: Annotated[list[int], Form()],
                     supplier_ids: Annotated[list[int], Form()], refresh: Annotated[bool, Form()] = False) -> dict:
    from app.services.search_jobs import search_jobs
    ids, sources = list(dict.fromkeys(item_ids)), list(dict.fromkeys(supplier_ids))
    if not 1 <= len(ids) <= 100 or not 1 <= len(sources) <= 5:
        raise HTTPException(422, 'Selecciona 1 a 100 artigos e 1 a 5 fornecedores.')
    with SessionLocal() as session:
        get_request(session, request_id)
        items = list(session.scalars(select(PurchaseRequestItem).where(PurchaseRequestItem.id.in_(ids), PurchaseRequestItem.purchase_request_id == request_id)))
        suppliers = list(session.scalars(select(Supplier).where(Supplier.id.in_(sources))))
        if len(items) != len(ids) or len(suppliers) != len(sources):
            raise HTTPException(422, 'Artigos ou fornecedores inválidos.')
        if any(not s.active or not s.scraping_enabled or not s.website for s in suppliers):
            raise HTTPException(422, 'Os fornecedores devem estar activos e permitir leitura.')
    try:
        return search_jobs.start(request_id, ids, sources, refresh, SessionLocal)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None


@router.get('/requests/{request_id}/search-jobs')
def search_job_status(request_id: int, response: Response) -> dict:
    from app.services.search_jobs import search_jobs
    with SessionLocal() as session:
        get_request(session, request_id)
    response.headers['Cache-Control'] = 'no-store'
    return search_jobs.status(request_id)


@router.post('/requests/{request_id}/search-jobs/cancel')
def cancel_search_job(request_id: int) -> dict:
    from app.services.search_jobs import search_jobs
    with SessionLocal() as session:
        get_request(session, request_id)
    try:
        return search_jobs.cancel(request_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from None
