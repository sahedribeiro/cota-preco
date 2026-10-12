from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from app.services.comparison_service import normalize
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import ProformaImport, PurchaseRequestItem, Supplier, SupplierOffer, UsageRecord, utc_now
from app.routes.requests import get_request
from app.routes.web import ai_service, templates
from app.services.ai_service import AIService, AIServiceError
from app.services.comparison_documents import extract_document
from app.services.procurement_matrix import prepare_source, store_source
from app.services.file_storage_service import DocumentError, FileStorageService

router = APIRouter()


def get_import(session: Session, request_id: int, import_id: int) -> ProformaImport:
    record = session.get(ProformaImport, import_id)
    if record is None or record.purchase_request_id != request_id:
        raise HTTPException(404, 'Proforma não encontrada nesta requisição.')
    return record


@router.get('/requests/{request_id}/proformas', response_class=HTMLResponse)
def index(request: Request, request_id: int) -> HTMLResponse:
    with SessionLocal() as session:
        purchase = get_request(session, request_id)
        imports = session.scalars(select(ProformaImport).where(ProformaImport.purchase_request_id == request_id).order_by(ProformaImport.id.desc())).all()
        return templates.TemplateResponse(request=request, name='proformas.html', context={'purchase':purchase, 'imports':imports})


@router.post('/requests/{request_id}/proformas')
def extract(request: Request, request_id: int, service: Annotated[AIService, Depends(ai_service)],
            document: Annotated[UploadFile, File()]) -> HTMLResponse:
    storage = FileStorageService()
    path = None
    try:
        with SessionLocal() as session:
            get_request(session, request_id)
        path = storage.save(document, allow_images=True)
        with SessionLocal() as session:
            def record_usage(result):
                session.add(UsageRecord(provider='openai', model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens))
                session.commit()
            draft = extract_document(path, document.filename or path.name, 'supplier', service, record_usage)
            draft['storage_file'] = path.name
            source = prepare_source(draft)
            store_source(session, source)
            record = ProformaImport(purchase_request_id=request_id, stored_filename=path.name, draft_json=draft)
            session.add(record)
            session.commit()
            import_id = record.id
        storage.mark(path, 'proforma_review_pending')
        return RedirectResponse(f'/requests/{request_id}/proformas/{import_id}', status_code=303)
    except (DocumentError, AIServiceError, SQLAlchemyError) as error:
        if path:
            storage.mark(path, 'proforma_failed')
        with SessionLocal() as session:
            purchase = get_request(session, request_id)
            message = 'Não foi possível guardar a extracção. A chamada à IA pode ter sido cobrada; verifica os documentos importados antes de reenviar.' if isinstance(error, SQLAlchemyError) else str(error)
            return templates.TemplateResponse(request=request, name='proformas.html', status_code=422,
                                              context={'purchase':purchase, 'imports':[], 'error':message})
    finally:
        document.file.close()


@router.get('/requests/{request_id}/proformas/{import_id}', response_class=HTMLResponse)
def review(request: Request, request_id: int, import_id: int) -> HTMLResponse:
    with SessionLocal() as session:
        purchase = get_request(session, request_id)
        record = get_import(session, request_id, import_id)
        items = session.scalars(select(PurchaseRequestItem).where(PurchaseRequestItem.purchase_request_id == request_id)).all()
        suppliers = session.scalars(select(Supplier).where(Supplier.active.is_(True)).order_by(Supplier.name)).all()
        saved = {o.document_line_key: o for o in session.scalars(select(SupplierOffer).where(SupplierOffer.document_line_key.like(f'{import_id}:%')))}
        supplier_ids = {o.supplier_id for o in saved.values()}
        matching_suppliers = [s.id for s in suppliers if normalize(s.name) == normalize(record.draft_json.get('supplier_name') or '')]
        default_supplier = next(iter(supplier_ids)) if len(supplier_ids) == 1 else matching_suppliers[0] if len(matching_suppliers) == 1 else None
        suggestions = {}
        for index, line in enumerate(record.draft_json['lines']):
            matches = [item.id for item in items if
                       (normalize(line.get('reference') or '') and normalize(line['reference']) == normalize(item.reference or '') and normalize(line['reference']) != normalize(item.brand or ''))
                       or (normalize(line['description']) in {normalize(item.description), normalize(item.normalized_description or '')}
                           and not (line.get('reference') and item.reference and normalize(line['reference']) != normalize(item.reference)))]
            suggestions[index] = matches[0] if len(matches) == 1 else None
        return templates.TemplateResponse(request=request, name='proforma_review.html', context={
            'purchase':purchase, 'record':record, 'draft':record.draft_json,
            'items':items, 'suppliers':suppliers, 'saved':saved, 'suggestions':suggestions,
            'default_supplier':default_supplier, 'default_reviewer':next(iter(saved.values())).evidence_json.get('reviewer', '') if saved else '',
        })


@router.get('/requests/{request_id}/proformas/{import_id}/document')
def download(request_id: int, import_id: int) -> FileResponse:
    with SessionLocal() as session:
        record = get_import(session, request_id, import_id)
        path = settings.upload_path / record.stored_filename
        if not path.is_file():
            raise HTTPException(404, 'Ficheiro original indisponível.')
        return FileResponse(path, filename=path.name, media_type='application/octet-stream')


@router.post('/requests/{request_id}/proformas/{import_id}/lines/{line_index}')
def save_line(request_id: int, import_id: int, line_index: int,
              item_id: Annotated[int, Form()], supplier_id: Annotated[int, Form()],
              product_name: Annotated[str, Form(min_length=1, max_length=300)],
              unit_price: Annotated[Decimal, Form(gt=0, lt=10**16, decimal_places=2)],
              currency: Annotated[str, Form(pattern=r'^[A-Z]{3}$')],
              vat: Annotated[Literal['unknown', 'included', 'excluded'], Form()],
              reviewer: Annotated[str, Form(min_length=1, max_length=100)],
              notes: Annotated[str, Form(max_length=2000)] = '',
              confirmed: Annotated[Literal['yes'], Form()] = 'yes') -> RedirectResponse:
    if not all(value.strip() for value in (product_name, reviewer)):
        raise HTTPException(422, 'Preenche descrição e nome.')
    with SessionLocal() as session:
        record = get_import(session, request_id, import_id)
        lines = record.draft_json['lines']
        if not 0 <= line_index < len(lines):
            raise HTTPException(404, 'Linha inexistente.')
        item = session.get(PurchaseRequestItem, item_id)
        supplier = session.get(Supplier, supplier_id)
        if item is None or item.purchase_request_id != request_id or supplier is None or not supplier.active:
            raise HTTPException(422, 'Artigo ou fornecedor inválido.')
        key = f'{import_id}:{line_index}'
        if not session.scalar(select(SupplierOffer.id).where(SupplierOffer.document_line_key == key)):
            add_offer(session, record, line_index, item, supplier, product_name, unit_price, currency, vat, reviewer, notes)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if not session.scalar(select(SupplierOffer.id).where(SupplierOffer.document_line_key == key)):
                    raise
    return RedirectResponse(f'/requests/{request_id}/proformas/{import_id}#line-{line_index}', status_code=303)


def add_offer(session: Session, record: ProformaImport, line_index: int, item: PurchaseRequestItem,
              supplier: Supplier, product_name: str, unit_price: Decimal, currency: str,
              vat: str, reviewer: str, notes: str) -> None:
    import_id = record.id
    key = f'{import_id}:{line_index}'
    session.add(SupplierOffer(document_line_key=key, purchase_request_item_id=item.id, supplier_id=supplier.id,
                product_name=product_name.strip(), unit_price=unit_price, currency=currency,
                vat_included={'included':True, 'excluded':False}.get(vat), match_confidence=0,
                evidence_json={'source_kind':'proforma', 'import_id':import_id, 'line_index':line_index,
                    'document_reference':record.draft_json.get('document_reference'),
                    'document_date':record.draft_json.get('document_date'), 'valid_until':record.draft_json.get('valid_until'),
                    'original_line':record.draft_json['lines'][line_index], 'reviewer':reviewer.strip(), 'notes':notes.strip(),
                    'reviewed_at':utc_now().isoformat(), 'restricted':True}))


class ImportLineInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    item_id: int
    product_name: str = Field(min_length=1, max_length=300)
    unit_price: Decimal = Field(gt=0, lt=10**16, decimal_places=2)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    vat: Literal['unknown', 'included', 'excluded']
    notes: str = Field(default='', max_length=2000)


@router.post('/requests/{request_id}/proformas/{import_id}/save')
async def save_batch(request: Request, request_id: int, import_id: int) -> RedirectResponse:
    form = await request.form()
    reviewer = str(form.get('reviewer', '')).strip()
    if not 1 <= len(reviewer) <= 100:
        raise HTTPException(422, 'Indica uma vez o nome de quem reviu.')
    try:
        supplier_id = int(str(form.get('supplier_id', '')))
    except ValueError:
        raise HTTPException(422, 'Escolhe o fornecedor da proforma.') from None
    with SessionLocal() as session:
        record = get_import(session, request_id, import_id)
        supplier = session.get(Supplier, supplier_id)
        if supplier is None or not supplier.active:
            raise HTTPException(422, 'Fornecedor inválido ou inactivo.')
        saved = set(session.scalars(select(SupplierOffer.document_line_key).where(SupplierOffer.document_line_key.like(f'{import_id}:%'))))
        for index, _ in enumerate(record.draft_json['lines']):
            prefix = f'line_{index}_'
            if f'{import_id}:{index}' in saved or not form.get(prefix+'item_id'):
                continue
            try:
                data = ImportLineInput.model_validate({field: form.get(prefix+field, '') for field in ImportLineInput.model_fields})
            except ValidationError:
                raise HTTPException(422, f'Linha {index+1}: verifica produto, preço unitário, moeda e observações. Nenhuma nova linha foi guardada.') from None
            item = session.get(PurchaseRequestItem, data.item_id)
            if item is None or item.purchase_request_id != request_id:
                raise HTTPException(422, f'Linha {index+1}: artigo inválido. Nenhuma nova linha foi guardada.')
            add_offer(session, record, index, item, supplier, data.product_name, data.unit_price,
                      data.currency, data.vat, reviewer, data.notes)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, 'A proforma foi alterada noutro separador. Recarrega antes de guardar.') from None
    return RedirectResponse(f'/requests/{request_id}/proformas/{import_id}', status_code=303)
