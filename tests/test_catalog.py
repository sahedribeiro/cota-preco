from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select,func
from app.models import Supplier,PriceRecord,SupplierProduct,CatalogImport,utc_now
from app.services.catalog_service import add_price,current_price,price_state
from app.services.catalog_documents import text_blocks
from tests.test_requests import client
import app.routes.requests as requests
import app.routes.catalog as routes


def test_catalog_csv_mapping_history_and_review(client,monkeypatch):
    monkeypatch.setattr(routes,'SessionLocal',requests.SessionLocal)
    with requests.SessionLocal() as s:
        supplier=Supplier(name='Loja');s.add(supplier);s.commit();sid=supplier.id
    csv='Descricao;Preco;Moeda;Unidade\nPC;100,50;AOA;un\nMonitor;invalido;AOA;un\n'
    response=client.post('/catalog/import',data={'supplier_id':sid},files={'document':('precos.csv',csv.encode(),'text/csv')})
    assert response.status_code==200 and '2 linhas' in response.text
    with requests.SessionLocal() as s: rid=s.scalar(select(CatalogImport.id))
    data={'supplier_id':sid,'description':'0','unit_price':'1','currency':'2','unit':'3','separator':','}
    assert client.post(f'/catalog/imports/{rid}/save',data=data).status_code==200
    assert client.post(f'/catalog/imports/{rid}/save',data=data).status_code==200
    with requests.SessionLocal() as s:
        records=list(s.scalars(select(PriceRecord).order_by(PriceRecord.id)))
        assert len(records)==2 and records[0].unit_price==Decimal('100.50')
        assert records[1].unit_price is None and records[1].status=='review'
        pending_id=records[1].id;pid=records[1].supplier_product_id
    assert client.post(f'/catalog/prices/{pending_id}/review',data={'unit_price':'200','currency':'AOA'}).status_code==200
    with requests.SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(PriceRecord))==3
        assert s.get(PriceRecord,pending_id).unit_price is None
    assert client.get(f'/catalog/products/{pid}').status_code==200


def test_price_history_validity_and_stale(migrated_db):
    from sqlalchemy.orm import Session
    engine,_=migrated_db
    with Session(engine) as s:
        supplier=Supplier(name='History');s.add(supplier);s.flush()
        a=add_price(s,supplier.id,dict(description='PC',unit_price='100',currency='AOA'),'a','manual','Teste')
        b=add_price(s,supplier.id,dict(description='PC',unit_price='120',currency='AOA'),'b','manual','Teste')
        a.price_date=utc_now()-timedelta(days=90)
        b.price_date=utc_now();b.valid_until=utc_now()-timedelta(days=1)
        s.commit()
        assert current_price([a,b]).id==a.id and price_state(a)=='Desactualizado'
        assert add_price(s,supplier.id,{},'a','manual','Teste').id==a.id


def test_large_document_blocks_do_not_truncate():
    text='\n'.join('Produto '+str(i) for i in range(1500))
    assert '\n'.join(text_blocks(text))==text

from tests.test_database import migrated_db


def test_catalog_extraction_resumes_without_repeating_blocks(client,monkeypatch):
    from types import SimpleNamespace
    from app.main import app
    from app.routes.web import ai_service
    from app.services.ai_service import AIServiceError
    monkeypatch.setattr(routes,'SessionLocal',requests.SessionLocal)
    calls=[]
    class Fake:
        def extract_comparison_block(self,text,filename,role,image=None):
            calls.append(text)
            if len(calls)==2: raise AIServiceError('Falha simulada')
            draft=dict(supplier_name='Vendedor',supplier_tax_id=None,supplier_email=None,supplier_phone=None,document_date=None,valid_until=None,warnings='',
                       lines=[dict(description=text[:20],reference=None,unit='un',unit_price='10',currency='AOA',vat_included=None,confidence=95,evidence=text[:30])])
            return SimpleNamespace(response=SimpleNamespace(model_dump=lambda:draft),model='mock',input_tokens=10,output_tokens=10)
    app.dependency_overrides[ai_service]=Fake
    try:
        text=('\n'.join(f'Produto de exemplo {i}' for i in range(700))).encode()
        assert client.post('/catalog/import',files={'document':('large.txt',text)}).status_code==200
        with requests.SessionLocal() as s: rid=s.scalar(select(CatalogImport.id))
        assert client.post(f'/catalog/imports/{rid}/extract').status_code==422
        first=calls[0]
        assert client.post(f'/catalog/imports/{rid}/extract').status_code==200
        assert calls.count(first)==1
        assert client.post(f'/catalog/imports/{rid}/save',data={'new_supplier':'Vendedor'}).status_code==200
        with requests.SessionLocal() as s:
            assert s.scalar(select(func.count()).select_from(PriceRecord))>2
    finally: app.dependency_overrides.pop(ai_service,None)


def test_scanned_pdf_uses_image_blocks(tmp_path):
    import pymupdf
    from app.services.catalog_documents import document_blocks
    path=tmp_path/'scan.pdf'
    with pymupdf.open() as doc:
        doc.new_page();doc.new_page();doc.save(path)
    blocks=list(document_blocks(path))
    assert len(blocks)==2 and all(image.startswith(b'\x89PNG') for _,image in blocks)


def test_catalog_migration_preserves_legacy_data(migrated_db):
    from alembic import command
    from sqlalchemy.orm import Session
    from app.models import PurchaseRequest,PurchaseRequestItem,SupplierOffer,ProformaImport
    engine,config=migrated_db
    command.downgrade(config,'0007')
    with Session(engine) as s:
        supplier=Supplier(name='Antigo');purchase=PurchaseRequest(title='Original')
        s.add_all([supplier,purchase]);s.flush()
        item=PurchaseRequestItem(purchase_request_id=purchase.id,description='PC');s.add(item);s.flush()
        s.add(SupplierOffer(supplier_id=supplier.id,purchase_request_item_id=item.id,product_name='PC',unit_price=Decimal('125.50'),currency='AOA',match_confidence=90,evidence_json={'source':'legacy'}))
        s.add(ProformaImport(purchase_request_id=purchase.id,stored_filename='legacy.pdf',draft_json={'supplier_name':'Antigo','lines':[{'description':'PC','unit_price':'125.50'}]}))
        s.commit()
    command.upgrade(config,'head')
    with Session(engine) as s:
        assert s.scalar(select(SupplierOffer.unit_price))==Decimal('125.50')
        assert s.scalar(select(PriceRecord.unit_price))==Decimal('125.50')
        assert s.scalar(select(CatalogImport)).draft_json['lines'][0]['description']=='PC'
        assert s.scalar(select(ProformaImport.stored_filename))=='legacy.pdf'
