from decimal import Decimal
import json
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import PurchaseRequest, PurchaseRequestItem, Supplier, SupplierOffer, PriceSearch, UsageRecord
from app.routes.web import templates
from app.services.ai_service import AIService, AIServiceError
from app.services.search_jobs import search_jobs
from app.services.comparison_service import comparison_values
from app.services.chat_comparison import price_matrix

router = APIRouter()


def begin_chat_search(request: Request, service: AIService, message: str, supplier_ids: list[int]) -> HTMLResponse:
    ids = list(dict.fromkeys(supplier_ids))
    if not message.strip() or not 1 <= len(ids) <= 5:
        return HTMLResponse('Indica os produtos e selecciona 1 a 5 fornecedores.',status_code=422,headers={'X-Chat-Error':'1'})
    with SessionLocal() as session:
        suppliers = list(session.scalars(select(Supplier).where(Supplier.id.in_(ids))))
        if len(suppliers)!=len(ids) or any(not s.active or not s.scraping_enabled or not s.website for s in suppliers):
            return HTMLResponse('Fornecedor indisponível para leitura.',status_code=422,headers={'X-Chat-Error':'1'})
    try:
        result=service.respond('Organiza os artigos explicitamente pedidos neste texto, sem inventar preços: '+message, [])
    except AIServiceError as error:
        return templates.TemplateResponse(request=request,name='error.html',context={'error':str(error)},status_code=503)
    with SessionLocal() as session:
        session.add(UsageRecord(provider='openai',model=result.model,input_tokens=result.input_tokens,output_tokens=result.output_tokens))
        draft=result.response.purchase_request
        if not draft or not draft.items:
            session.commit()
            return HTMLResponse('Não identifiquei artigos. Indica produto, modelo e características para pesquisar.',status_code=422,headers={'X-Chat-Error':'1'})
        purchase=PurchaseRequest(title=draft.title,description='\n'.join(draft.conditions),status='draft')
        session.add(purchase);session.flush()
        items=[]
        for row in draft.items:
            item=PurchaseRequestItem(purchase_request_id=purchase.id,description=row.description,normalized_description=row.normalized_description,
                brand=row.brand,model=row.model,reference=row.reference,quantity=Decimal(str(row.quantity)) if row.quantity else None,
                unit=row.unit,specifications_json={s.name:s.value for s in row.specifications})
            session.add(item);items.append(item)
        session.flush(); item_ids=[item.id for item in items]; request_id=purchase.id
        session.commit()
    try:
        search_jobs.start(request_id,item_ids,ids,False,SessionLocal)
    except ValueError:
        return HTMLResponse(f'Existe outra pesquisa em curso. O pedido ficou guardado: <a href="/requests/{request_id}">abrir pedido</a>.',status_code=409,headers={'X-Chat-Error':'1'})
    return templates.TemplateResponse(request=request,name='chat_search.html',context={'request_id':request_id})


@router.get('/chat-searches/{request_id}')
def chat_search_status(request: Request, request_id: int, response: Response) -> dict:
    response.headers['Cache-Control']='no-store'
    with SessionLocal() as session:
        purchase=session.get(PurchaseRequest,request_id)
        if purchase is None: raise HTTPException(404,'Pedido não encontrado.')
        status=search_jobs.status(request_id)
        if status['state'] in {'running','stopping','idle'}: return status
        items=list(session.scalars(select(PurchaseRequestItem).where(PurchaseRequestItem.purchase_request_id==request_id)))
        documents={}
        warnings=[]
        for item in items:
            search=session.scalar(select(PriceSearch).where(PriceSearch.purchase_request_item_id==item.id).order_by(PriceSearch.id.desc()))
            if not search: continue
            for source in search.raw_result_json.get('sources',[]):
                warnings.append(item.description+' · '+source['name']+': '+source['message'])
            for sid in search.raw_result_json.get('supplier_ids',[]):
                supplier=session.get(Supplier,sid)
                if supplier: documents.setdefault(sid,{'label':supplier.name,'draft':{'lines':[],'warnings':'Preços web observados; candidatos precisam de confirmação.'}})
            for offer in session.scalars(select(SupplierOffer).where(SupplierOffer.price_search_id==search.id)):
                if offer.supplier_id not in documents: continue
                values=comparison_values(offer)
                documents[offer.supplier_id]['draft']['lines'].append(dict(description=item.description,
                    reference=item.reference,unit=item.unit if values['matched'] else None,
                    quantity=str(item.quantity) if item.quantity else None,unit_price=str(offer.unit_price) if offer.unit_price else None,
                    currency=offer.currency if offer.currency!='UNK' else None,vat_included=offer.vat_included,
                    evidence=f'{offer.product_name} | Confiança: {offer.match_confidence}% | {offer.product_url} | {offer.observed_at.isoformat()} UTC'))
        docs=list(documents.values())
        report={'message':purchase.title,'documents':docs,'rows':price_matrix(docs),'web_warnings':warnings,'kind':'web'}
        report_id=uuid4().hex
        settings.upload_path.mkdir(parents=True,exist_ok=True)
        (settings.upload_path/f'{report_id}.comparison.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
        status['html']=templates.get_template('chat_comparison.html').render(report=report,report_id=report_id)
        return status
