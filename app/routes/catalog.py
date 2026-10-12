import hashlib
from typing import Annotated
from decimal import Decimal
from fastapi import APIRouter, Request, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select, or_
from app.core.database import SessionLocal
from app.core.config import settings
from app.models import Product,SupplierProduct,PriceRecord,CatalogImport,Supplier,UserPreference,UsageRecord
from app.routes.web import templates,ai_service
from app.services.ai_service import AIService,AIServiceError
from app.services.catalog_service import add_price,amount,current_price,price_state
from app.services.catalog_documents import table_rows
from app.services.comparison_documents import extract_document
from app.services.procurement_matrix import prepare_source, store_source
from app.services.file_storage_service import FileStorageService,DocumentError

router=APIRouter()
FIELDS=('description','reference','unit_price','currency','unit','vat_included','vat_rate','price_date','valid_until')


@router.get('/catalog')
def catalog(request: Request,q: str='',offset: int=0):
    with SessionLocal() as session:
        query=select(SupplierProduct).outerjoin(Product,SupplierProduct.product_id==Product.id)
        if q: query=query.where(or_(SupplierProduct.description.ilike('%'+q[:200]+'%'),Product.name.ilike('%'+q[:200]+'%'),SupplierProduct.reference.ilike('%'+q[:200]+'%')))
        products=list(session.scalars(query.order_by(SupplierProduct.id.desc()).offset(max(offset,0)).limit(100)))
        prices={p.id:current_price(list(session.scalars(select(PriceRecord).where(PriceRecord.supplier_product_id==p.id)))) for p in products}
        matrix={}
        for product in products:
            if product.product_id:
                canonical=session.get(Product,product.product_id)
                row=matrix.setdefault(product.product_id,{'name':canonical.name,'cells':{}})
                row['cells'].setdefault(product.supplier_id,[]).append(prices[product.id])
        return templates.TemplateResponse(request=request,name='catalog.html',context=dict(products=products,prices=prices,matrix=matrix,state=price_state,q=q,offset=max(offset,0),
            suppliers={s.id:s for s in session.scalars(select(Supplier))},imports=list(session.scalars(select(CatalogImport).order_by(CatalogImport.id.desc()).limit(30)))))


@router.post('/catalog/import')
def upload(document: Annotated[UploadFile,File()],supplier_id: Annotated[str,Form()]=''):
    try:
        path=FileStorageService().save(document,allow_images=True)
        kind=path.suffix.lstrip('.')
        draft={'file_hash':hashlib.sha256(path.read_bytes()).hexdigest(), 'filename':document.filename or path.name}
        if kind in {'xlsx','csv'}:
            header,rows=table_rows(path);draft.update(headers=header,rows=rows)
        with SessionLocal() as session:
            supplier=session.get(Supplier,int(supplier_id)) if supplier_id.isdigit() else None
            if supplier_id and supplier is None: raise HTTPException(422,'Fornecedor inválido.')
            record=CatalogImport(filename=path.name,source_kind='excel' if kind=='xlsx' else kind,supplier_id=supplier.id if supplier else None,draft_json=draft)
            session.add(record);session.commit();rid=record.id
        return RedirectResponse(f'/catalog/imports/{rid}',303)
    except DocumentError as error: raise HTTPException(422,str(error)) from None
    finally: document.file.close()


@router.get('/catalog/imports/{import_id}')
def import_detail(request: Request,import_id:int):
    with SessionLocal() as session:
        record=session.get(CatalogImport,import_id)
        if not record: raise HTTPException(404)
        mapping=session.scalar(select(UserPreference).where(UserPreference.key==f'catalog_mapping:{record.supplier_id}'))
        return templates.TemplateResponse(request=request,name='catalog_import.html',context=dict(record=record,fields=FIELDS,mapping=mapping.value_json if mapping else {},
            suppliers=list(session.scalars(select(Supplier).order_by(Supplier.name)))))


@router.post('/catalog/imports/{import_id}/extract')
def extract_import(import_id:int,service:Annotated[AIService,Depends(ai_service)]):
    with SessionLocal() as session:
        record=session.get(CatalogImport,import_id)
        if not record: raise HTTPException(404)
        if record.status=='imported' or record.draft_json.get('extracted'): return RedirectResponse(f'/catalog/imports/{import_id}',303)
        draft=dict(record.draft_json)
        try:
            def record_usage(result):
                session.add(UsageRecord(provider='openai',model=result.model,input_tokens=result.input_tokens,output_tokens=result.output_tokens))
                session.commit()
            extracted=extract_document(settings.upload_path/record.filename,
                draft.get('filename',record.filename),'supplier',service,record_usage)
            draft.update(extracted)
            draft['extracted']=True;record.draft_json=draft;session.commit()
        except (DocumentError,AIServiceError) as error:
            raise HTTPException(422,f'{error} Blocos concluídos guardados; podes retomar a extracção.') from None
    return RedirectResponse(f'/catalog/imports/{import_id}',303)


@router.post('/catalog/imports/{import_id}/save')
async def import_save(request:Request,import_id:int):
    form=await request.form()
    with SessionLocal() as session:
        record=session.get(CatalogImport,import_id)
        if not record: raise HTTPException(404)
        if record.status=='imported': return RedirectResponse('/catalog',303)
        draft=record.draft_json
        supplier_id=str(form.get('supplier_id',''))
        supplier=session.get(Supplier,int(supplier_id)) if supplier_id.isdigit() else None
        if supplier is None:
            name=str(form.get('new_supplier','')).strip()
            if not name or len(name)>200: raise HTTPException(422,'Selecciona um fornecedor ou confirma o nome proposto.')
            supplier=next((s for s in session.scalars(select(Supplier)) if s.name.strip().casefold()==name.casefold()),None)
            if supplier is None:
                supplier=Supplier(name=name,tax_id=draft.get('supplier_tax_id'),email=draft.get('supplier_email'),phone=draft.get('supplier_phone'),reviewed=True,source_kind='document')
                session.add(supplier);session.flush()
        if 'rows' in draft:
            mapping={f:str(form.get(f,'')) for f in FIELDS}
            if not mapping['description'].isdigit() or not mapping['unit_price'].isdigit(): raise HTTPException(422,'Mapeia descrição e preço unitário.')
            separator=str(form.get('separator','.'))
            lines=[]
            for number,row in enumerate(draft['rows'],2):
                line={f:row[int(col)] if col.isdigit() and int(col)<len(row) else None for f,col in mapping.items()}
                price=amount(line['unit_price'],separator)
                line['unit_price']=str(price) if price else None
                vat=str(line['vat_included'] or '').strip().casefold()
                line['vat_included']=True if vat in {'sim','true','incluido','incluído','1'} else False if vat in {'não','nao','false','excluido','excluído','0'} else None
                default_currency=str(form.get('default_currency','AOA')).strip().upper()
                if not line['currency'] and default_currency:
                    line['currency_assumed']=True
                line['currency']=line['currency'] or default_currency or None
                line['evidence']=f'Linha {number}: '+' | '.join(row)
                line['raw_cells']=row;lines.append(line)
            pref=session.scalar(select(UserPreference).where(UserPreference.key==f'catalog_mapping:{supplier.id}'))
            if not pref: pref=UserPreference(key=f'catalog_mapping:{supplier.id}');session.add(pref)
            pref.value_json={**mapping,'separator':separator}
        else:
            if not draft.get('extracted') and not draft.get('legacy_import_id'): raise HTTPException(422,'Conclui primeiro a extracção.')
            lines=draft.get('lines',[])
        if draft.get('file_hash'):
            source=prepare_source(dict(draft,lines=lines,supplier_name=supplier.name,
                filename=draft.get('filename',record.filename),storage_file=record.filename),
                default_currency=str(form.get('default_currency','AOA')).strip().upper())
            store_source(session,source,supplier_id=supplier.id)
        else:
            # Preserve identities for imports saved before shared extraction existed.
            for index,line in enumerate(lines):
                if draft.get('conflict'): line={**line,'confidence':0}
                add_price(session,supplier.id,line,f'import:{record.id}:{index}',record.source_kind,record.filename,
                          line.get('price_date') or draft.get('document_date'),line.get('valid_until') or draft.get('valid_until'))
        record.supplier_id=supplier.id;record.status='imported';session.commit()
    return RedirectResponse('/catalog',303)


@router.get('/catalog/products/{product_id}')
def product_detail(request:Request,product_id:int):
    with SessionLocal() as session:
        product=session.get(SupplierProduct,product_id)
        if not product: raise HTTPException(404)
        records=list(session.scalars(select(PriceRecord).where(PriceRecord.supplier_product_id==product_id).order_by(PriceRecord.id.desc())))
        related = list(session.scalars(select(SupplierProduct).where(SupplierProduct.product_id == product.product_id))) if product.product_id else [product]
        statistics = []
        for related_product in related:
            history = list(session.scalars(select(PriceRecord).where(PriceRecord.supplier_product_id == related_product.id, PriceRecord.status == 'accepted').order_by(PriceRecord.price_date, PriceRecord.id)))
            groups = {}
            for price in history:
                if price.unit_price is not None:
                    groups.setdefault((price.currency, price.vat_included), []).append(price)
            for (currency, vat), observations in groups.items():
                values = [price.unit_price for price in observations]
                lower, upper = min(values), max(values)
                points = ' '.join(f'{10+i*280/max(1,len(values)-1):.1f},{80-float((value-lower)/(upper-lower or 1))*60:.1f}' for i,value in enumerate(values))
                statistics.append(dict(supplier=session.get(Supplier,related_product.supplier_id).name, currency=currency, vat=vat,
                    minimum=lower, maximum=upper, average=sum(values)/len(values), last=observations[-1].unit_price,
                    points=points, count=len(values), first_date=observations[0].price_date, last_date=observations[-1].price_date))
        return templates.TemplateResponse(request=request,name='catalog_product.html',context=dict(product=product,records=records,state=price_state,
            statistics=statistics, supplier=session.get(Supplier,product.supplier_id),canonical=list(session.scalars(select(Product).order_by(Product.name)))))


@router.post('/catalog/products/{product_id}/associate')
def associate(product_id:int,canonical_id:Annotated[str,Form()]='',name:Annotated[str,Form(max_length=500)]=''):
    with SessionLocal() as session:
        product=session.get(SupplierProduct,product_id)
        if not product: raise HTTPException(404)
        canonical=session.get(Product,int(canonical_id)) if canonical_id.isdigit() else None
        if canonical_id and canonical is None: raise HTTPException(422,'Produto canónico inválido.')
        if name.strip():
            canonical=Product(name=name.strip(),reference=product.reference,unit=product.unit)
            session.add(canonical);session.flush()
        product.product_id=canonical.id if canonical else None
        session.commit()
    return RedirectResponse(f'/catalog/products/{product_id}',303)


@router.post('/catalog/prices/{price_id}/review')
def review_price(price_id:int,unit_price:Annotated[str,Form()],currency:Annotated[str,Form()],action:Annotated[str,Form()]='accept'):
    with SessionLocal() as session:
        record=session.get(PriceRecord,price_id)
        if not record: raise HTTPException(404)
        if action=='reject': record.status='rejected'
        else:
            price=amount(unit_price)
            if not price or len(currency)!=3 or not currency.isalpha(): raise HTTPException(422,'Preço ou moeda inválidos.')
            product=session.get(SupplierProduct,record.supplier_product_id)
            # Corrections append a record; preserve the extracted value and evidence.
            corrected=PriceRecord(supplier_product_id=product.id,unit_price=price,currency=currency.upper(),vat_included=record.vat_included,vat_rate=record.vat_rate,
                price_date=record.price_date,valid_until=record.valid_until,source_kind=record.source_kind,source_reference=record.source_reference,
                evidence=record.evidence,origin_key=f'review:{record.id}',status='accepted',raw_json={'reviewed_record':record.id})
            if not session.scalar(select(PriceRecord.id).where(PriceRecord.origin_key==corrected.origin_key)): session.add(corrected)
            record.status='reviewed'
        session.commit();pid=record.supplier_product_id
    return RedirectResponse(f'/catalog/products/{pid}',303)


@router.post('/catalog/products/{product_id}/edit')
def edit_supplier_product(product_id:int,description:Annotated[str,Form(min_length=1,max_length=500)],
                          reference:Annotated[str,Form(max_length=150)]='',unit:Annotated[str,Form(max_length=40)]=''):
    import hashlib
    with SessionLocal() as session:
        product=session.get(SupplierProduct,product_id)
        if not product: raise HTTPException(404)
        description=description.strip()
        if not description: raise HTTPException(422,'Descrição obrigatória.')
        identity=hashlib.sha256(f'{product.supplier_id}|{description}|{reference}|{unit}'.encode()).hexdigest()
        duplicate=session.scalar(select(SupplierProduct.id).where(SupplierProduct.identity_key==identity,SupplierProduct.id!=product_id))
        if duplicate: raise HTTPException(409,'Já existe este produto do fornecedor. Associa ambos ao mesmo produto canónico.')
        product.description=description;product.reference=reference or None;product.unit=unit or None;product.identity_key=identity
        session.commit()
    return RedirectResponse(f'/catalog/products/{product_id}',303)
