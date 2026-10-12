from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.models import Supplier, UsageRecord
from app.routes.web import ai_service, templates
from app.schemas.supplier import SupplierInput, website_url
from app.services.ai_service import AIService, AIServiceError
from app.services.document_service import DocumentService, VisualProcessingRequired
from app.services.file_storage_service import DocumentError, FileStorageService
from app.services.website_service import WebsiteError, extract_website

router = APIRouter()


@router.get('/suppliers', response_class=HTMLResponse)
def directory(request: Request, q: str = '', state: str = 'active') -> HTMLResponse:
    with SessionLocal() as session:
        query = select(Supplier)
        if q.strip():
            term = f"%{q.strip()[:100]}%"
            query = query.where(or_(Supplier.name.ilike(term), Supplier.category.ilike(term), Supplier.tax_id.ilike(term)))
        if state != 'all':
            query = query.where(Supplier.active.is_(state != 'inactive'))
        suppliers = session.scalars(query.order_by(Supplier.name).limit(200)).all()
        return templates.TemplateResponse(request=request, name='suppliers.html', context={'suppliers': suppliers, 'q': q, 'state': state})


@router.get('/suppliers/new', response_class=HTMLResponse)
def new_supplier(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name='supplier_edit.html', context={'supplier': None, 'values': {}})


@router.get('/suppliers/{supplier_id}', response_class=HTMLResponse)
def edit_supplier(request: Request, supplier_id: int) -> HTMLResponse:
    with SessionLocal() as session:
        supplier = session.get(Supplier, supplier_id)
        if supplier is None:
            raise HTTPException(404, 'Fornecedor não encontrado.')
        return templates.TemplateResponse(request=request, name='supplier_edit.html', context={'supplier': supplier, 'values': supplier})


@router.post('/suppliers/save', response_class=HTMLResponse)
def save_supplier(request: Request, data: Annotated[SupplierInput, Form()], supplier_id: int | None = None) -> HTMLResponse:
    with SessionLocal() as session:
        supplier = session.get(Supplier, supplier_id) if supplier_id else None
        if supplier_id and supplier is None:
            raise HTTPException(404, 'Fornecedor não encontrado.')
        name_match = func.lower(Supplier.name) == data.name.lower()
        duplicate = session.scalar(select(Supplier).where(or_(name_match, Supplier.tax_id == data.tax_id) if data.tax_id else name_match).where(Supplier.id != (supplier_id or 0)))
        if duplicate:
            return templates.TemplateResponse(request=request, name='supplier_edit.html', status_code=409, context={
                'supplier': supplier, 'values': data, 'error': f'Já existe um fornecedor com este nome ou NIF: {duplicate.name}. Abre o registo existente.', 'duplicate': duplicate,
            })
        if supplier is None:
            supplier = Supplier(name=data.name)
            session.add(supplier)
        for field, value in data.model_dump().items():
            setattr(supplier, field, value.strip() or None if isinstance(value, str) else value)
        session.commit()
        return RedirectResponse(f'/suppliers/{supplier.id}', status_code=303)


@router.post('/suppliers/import', response_class=HTMLResponse)
def import_supplier(
    request: Request, service: Annotated[AIService, Depends(ai_service)],
    source_kind: Annotated[str, Form()],
    document: Annotated[UploadFile | None, File()] = None,
    url: Annotated[str, Form(max_length=1000)] = '',
    supplier_id: Annotated[int | None, Form()] = None,
) -> HTMLResponse:
    path = None
    storage = FileStorageService()
    warning = ''
    existing = None
    try:
        if source_kind == 'website':
            source = website_url(url)
            if not source:
                raise WebsiteError('Indica o URL da página a ler.')
            with SessionLocal() as session:
                if supplier_id:
                    existing = session.get(Supplier, supplier_id)
                    if existing is None or existing.website != source or not existing.active or not existing.scraping_enabled:
                        raise WebsiteError('Activa o fornecedor e permite a leitura do website antes de consultar.')
                    session.expunge(existing)
                for registered in session.scalars(select(Supplier).where(Supplier.website.is_not(None))):
                    if urlsplit(registered.website).hostname == urlsplit(source).hostname and (not registered.active or not registered.scraping_enabled):
                        raise WebsiteError('Este domínio pertence a um fornecedor com leitura desactivada. Actualiza o cadastro primeiro.')
            text, truncated = extract_website(source)
            warning = 'Foi lido apenas o início da página (8.000 caracteres). Confirma os dados ou usa a página de contactos.' if truncated else ''
        elif source_kind in {'purchase_order', 'proforma'} and document and document.filename:
            path = storage.save(document)
            text = DocumentService().extract(path)
            source = path.name
            storage.mark(path, 'supplier_extracted')
        else:
            raise DocumentError('Escolhe uma ordem de compra, proforma ou website.')
        result = service.extract_supplier(text, source_kind)
        values = result.response.model_dump()
        values.update(source_kind=source_kind, source_reference=source, active=True, reviewed=False, scraping_enabled=bool(existing and existing.scraping_enabled))
        try:
            with SessionLocal() as session:
                session.add(UsageRecord(provider='openai', model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens))
                session.commit()
        except SQLAlchemyError:
            warning += ' A resposta chegou, mas não foi possível registar o consumo localmente.'
        if path:
            storage.mark(path, 'supplier_review_pending')
        return templates.TemplateResponse(request=request, name='supplier_edit.html', context={
            'supplier': existing, 'values': values, 'warning': warning,
            'notice': 'Proposta extraída. Confirma quem é o fornecedor (não o comprador), revê os dados e guarda. Nada foi cadastrado automaticamente.',
        })
    except (DocumentError, WebsiteError, AIServiceError, ValueError) as error:
        if path:
            storage.mark(path, 'needs_visual_processing' if isinstance(error, VisualProcessingRequired) else 'supplier_import_failed')
        return templates.TemplateResponse(request=request, name='supplier_edit.html', status_code=422, context={'supplier': None, 'values': {}, 'error': str(error)})
    finally:
        if document:
            document.file.close()
