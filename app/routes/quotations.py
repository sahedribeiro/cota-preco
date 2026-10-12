from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4
from typing import Annotated
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Depends
from fastapi.responses import RedirectResponse, FileResponse
from sqlalchemy import select, delete
from app.core.database import SessionLocal
from app.core.config import settings
from app.models import PurchaseRequestItem, ProformaImport, SalesQuote, SalesQuoteLine, UserPreference, UsageRecord
from app.routes.requests import get_request
from app.routes.web import templates, ai_service
from app.services.ai_service import AIService, AIServiceError
from app.services.sales_quote import quote_options, suggested_options, selling_price
from app.services.file_storage_service import FileStorageService, DocumentError
from app.services.document_service import DocumentService

router=APIRouter()


@router.get('/requests/{request_id}/quotation')
def workspace(request: Request, request_id: int):
    with SessionLocal() as session:
        purchase=get_request(session,request_id)
        items=list(session.scalars(select(PurchaseRequestItem).where(PurchaseRequestItem.purchase_request_id==request_id)))
        options=quote_options(session,request_id)
        quote=session.scalar(select(SalesQuote).where(SalesQuote.purchase_request_id==request_id))
        lines=list(session.scalars(select(SalesQuoteLine).where(SalesQuoteLine.quote_id==quote.id))) if quote else []
        preference=session.scalar(select(UserPreference).where(UserPreference.key=='quote_company'))
        imports=list(session.scalars(select(ProformaImport).where(ProformaImport.purchase_request_id==request_id)))
        return templates.TemplateResponse(request=request,name='quotation.html',context=dict(purchase=purchase,items=items,options=options,
            suggestions={i.id:suggested_options(i,options) for i in items},quote=quote,selected={l.item_id:l.source_json['key'] for l in lines},
            company=quote.company_json if quote else preference.value_json if preference else {},imports=imports))


@router.post('/requests/{request_id}/quotation/documents')
def add_documents(request_id: int, documents: Annotated[list[UploadFile],File()],service: Annotated[AIService,Depends(ai_service)]):
    saved=0
    try:
        if not 1<=len(documents)<=10: raise DocumentError('Anexa até 10 ficheiros por envio; podes adicionar mais depois.')
        with SessionLocal() as session: get_request(session,request_id)
        storage=FileStorageService()
        for document in documents:
            path=storage.save(document)
            result=service.extract_proforma(DocumentService().extract(path))
            with SessionLocal() as session:
                session.add(ProformaImport(purchase_request_id=request_id,stored_filename=path.name,draft_json=result.response.model_dump()))
                session.add(UsageRecord(provider='openai',model=result.model,input_tokens=result.input_tokens,output_tokens=result.output_tokens))
                session.commit()
            saved+=1
    except (DocumentError,AIServiceError) as error:
        raise HTTPException(422,f'{saved} documentos já guardados. {error} Não reenvies os que foram guardados.') from None
    finally:
        for document in documents: document.file.close()
    return RedirectResponse(f'/requests/{request_id}/quotation',303)


@router.post('/requests/{request_id}/quotation')
async def save_quote(request: Request, request_id: int):
    form=await request.form()
    company={key:str(form.get(key,'')).strip() for key in ('company_name','tax_id','address','contacts')}
    customer=str(form.get('customer','')).strip(); terms=str(form.get('terms','')).strip()
    if any(len(v)>2000 for v in [*company.values(),customer,terms]): raise HTTPException(422,'Texto demasiado longo.')
    method=str(form.get('method',''))
    try:
        percentage=Decimal(str(form.get('percentage','')))
        selling_price(Decimal('1'),percentage,method)
        if percentage != percentage.quantize(Decimal('.001')): raise ValueError('Usa até três casas decimais no percentual.')
    except (InvalidOperation,ValueError) as error: raise HTTPException(422,'Percentual ou método inválido: '+str(error)) from None
    with SessionLocal() as session:
        get_request(session,request_id)
        options={o['key']:o for o in quote_options(session,request_id)}
        items=list(session.scalars(select(PurchaseRequestItem).where(PurchaseRequestItem.purchase_request_id==request_id)))
        chosen=[]
        for item in items:
            key=str(form.get(f'item_{item.id}',''))
            if not key: continue
            if key not in options: raise HTTPException(422,'Cotação inválida para este pedido.')
            if not item.quantity or not item.unit: raise HTTPException(422,f'Completa quantidade e unidade do artigo #{item.id}.')
            option=options[key]
            try: sale=selling_price(Decimal(option['cost']),percentage,method)
            except ValueError as error: raise HTTPException(422,str(error)) from None
            chosen.append((item,option,sale))
        if not chosen: raise HTTPException(422,'Selecciona pelo menos uma cotação.')
        quote=session.scalar(select(SalesQuote).where(SalesQuote.purchase_request_id==request_id))
        preference=session.scalar(select(UserPreference).where(UserPreference.key=='quote_company'))
        if preference and preference.value_json.get('logo'): company['logo']=preference.value_json['logo']
        if quote and quote.company_json.get('logo'): company['logo']=quote.company_json['logo']
        logo=form.get('logo')
        if logo and getattr(logo,'filename',''):
            data=await logo.read(2*1024*1024+1)
            if len(data)>2*1024*1024: raise HTTPException(422,'Logotipo: máximo 2 MB.')
            suffix='.png' if data.startswith(b'\x89PNG\r\n\x1a\n') else '.jpg' if data.startswith(b'\xff\xd8\xff') else None
            if not suffix: raise HTTPException(422,'Usa um logotipo PNG ou JPEG.')
            name=uuid4().hex+suffix
            settings.upload_path.mkdir(parents=True,exist_ok=True)
            (settings.upload_path/name).write_bytes(data);company['logo']=name
        if quote is None:
            quote=SalesQuote(purchase_request_id=request_id);session.add(quote)
        quote.company_json=company;quote.customer=customer;quote.terms=terms;quote.pricing_method=method;quote.percentage=percentage
        session.flush()
        session.execute(delete(SalesQuoteLine).where(SalesQuoteLine.quote_id==quote.id))
        for item,option,sale in chosen:
            session.add(SalesQuoteLine(quote_id=quote.id,item_id=item.id,description=item.description,unit=item.unit,quantity=item.quantity,
                currency=option['currency'],cost=Decimal(option['cost']),sale_price=sale,source_json=option))
        if preference is None: preference=UserPreference(key='quote_company');session.add(preference)
        preference.value_json=company
        session.commit()
    return RedirectResponse(f'/requests/{request_id}/quotation',303)


@router.get('/requests/{request_id}/quotation/preview')
def preview(request: Request,request_id: int):
    with SessionLocal() as session:
        purchase=get_request(session,request_id)
        quote=session.scalar(select(SalesQuote).where(SalesQuote.purchase_request_id==request_id))
        if quote is None: raise HTTPException(404,'Guarda primeiro o orçamento.')
        lines=list(session.scalars(select(SalesQuoteLine).where(SalesQuoteLine.quote_id==quote.id)))
        totals={}
        for line in lines:
            total=(line.quantity*line.sale_price).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            totals[line.currency]=totals.get(line.currency,Decimal('0'))+total
        return templates.TemplateResponse(request=request,name='quotation_preview.html',context=dict(purchase=purchase,quote=quote,lines=lines,totals=totals))


@router.get('/requests/{request_id}/quotation/logo')
def quote_logo(request_id: int):
    with SessionLocal() as session:
        quote=session.scalar(select(SalesQuote).where(SalesQuote.purchase_request_id==request_id))
        name=quote.company_json.get('logo') if quote else None
        if not name or Path(name).name!=name or Path(name).suffix not in {'.png','.jpg'}: raise HTTPException(404)
        path=settings.upload_path/name
        if not path.is_file(): raise HTTPException(404)
        return FileResponse(path,media_type='image/png' if path.suffix=='.png' else 'image/jpeg')
