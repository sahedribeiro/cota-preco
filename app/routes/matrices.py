import json
from html import escape
import re
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from app.core.config import settings
from app.models import UsageRecord, Supplier, PriceRecord, SupplierProduct, Product
from app.routes import web
from app.services.ai_service import AIService, AIServiceError
from app.services.file_storage_service import FileStorageService, DocumentError
from app.services.comparison_documents import extract_document
from app.services.procurement_matrix import prepare_source, store_source, catalog_sources, build_matrix, number, equivalent, assist_matching
from app.services.matrix_quote import calculate_quote
from app.services.matrix_exports import matrix_excel, matrix_pdf

router = APIRouter()


@router.get('/matrices')
def list_matrices(request: Request):
    reports = []
    for path in sorted(settings.upload_path.glob('*.comparison.json'), key=lambda file: file.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('version') == 2:
                reports.append(dict(id=UUID(path.name.split('.')[0]).hex, data=data))
        except (ValueError, OSError):
            continue
        if len(reports) == 50:
            break
    return web.templates.TemplateResponse(request=request, name='matrices.html', context={'reports': reports})


@router.post('/matrices/inspect')
def inspect_proformas(proformas: Annotated[list[UploadFile], File()], service: Annotated[AIService, Depends(web.ai_service)]):
    from html import escape
    if len(proformas) > 5:
        raise HTTPException(422, 'Anexa no máximo 5 proformas.')
    names = []
    def usage(result):
        with web.SessionLocal() as session:
            session.add(UsageRecord(provider='openai', model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens))
            session.commit()
    try:
        for upload in proformas:
            path = FileStorageService().save(upload, allow_images=True)
            draft = extract_document(path, upload.filename, 'supplier', service, usage)
            names.append(f"{escape(upload.filename)} → {escape(draft.get('supplier_name') or 'Fornecedor por confirmar')} · {len(draft['lines'])} artigos")
        return HTMLResponse('<br>'.join(names))
    except (DocumentError, AIServiceError) as error:
        return HTMLResponse(escape(str(error)), status_code=422)
    finally:
        for upload in proformas:
            upload.file.close()


def report_path(report_id: UUID):
    return settings.upload_path / f'{report_id.hex}.comparison.json'


def read_report(report_id: UUID) -> dict:
    path = report_path(report_id)
    if not path.exists():
        raise HTTPException(404, 'Comparação não encontrada.')
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('version') != 2:
        raise HTTPException(422, 'Cria uma nova comparação com a solicitação separada.')
    return report


def save_report(report_id: UUID, report: dict) -> None:
    path = report_path(report_id)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def instruction_options(message: str) -> dict:
    text = message.casefold()
    percent = re.search(r'(?:\+|markup\s*)(\d+(?:[.,]\d+)?)\s*%', text)
    company = re.search(r'em nome d[aeo]\s+([^\n]+)', message, re.I)
    return dict(strategy='highest' if 'mais alto' in text else 'lowest' if 'mais baixo' in text else '',
                markup=percent[1].replace(',', '.') if percent else '0', vat='14',
                company=company[1].strip() if company else '', customer='', tax_id='', address='', contacts='',
                validity='30 dias', delivery='', payment='', exemption='')


@router.post('/matrices')
def create_matrix(request: Request, service: Annotated[AIService, Depends(web.ai_service)],
                  message: Annotated[str, Form(max_length=12000)] = '',
                  solicitation: Annotated[UploadFile | None, File()] = None,
                  proformas: Annotated[list[UploadFile] | None, File()] = None,
                  use_proformas: Annotated[bool, Form()] = False,
                  use_catalog: Annotated[bool, Form()] = False,
                  use_online: Annotated[bool, Form()] = False,
                  catalog_days: Annotated[int, Form(ge=0, le=3650)] = 60,
                  default_currency: Annotated[str, Form(pattern='^(AOA|USD|EUR)?$')] = 'AOA',
                  supplier_ids: Annotated[list[int] | None, Form()] = None):
    uploads = [p for p in proformas or [] if p.filename]
    storage = FileStorageService()
    def usage(result):
        with web.SessionLocal() as session:
            session.add(UsageRecord(provider='openai', model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens))
            session.commit()
    try:
        if len(uploads) > 5:
            raise DocumentError('Anexa no máximo 5 proformas.')
        if not message.strip() and not (solicitation and solicitation.filename):
            raise DocumentError('Indica a solicitação do cliente em texto ou ficheiro.')
        if solicitation and solicitation.filename:
            path = storage.save(solicitation, allow_images=True)
            requested = extract_document(path, solicitation.filename, 'request', service, usage)
        else:
            # Reuse the same bounded extraction path for text-only requests.
            from io import BytesIO
            path = storage.save(UploadFile(filename='solicitacao.txt', file=BytesIO(message.encode())))
            requested = extract_document(path, 'solicitacao.txt', 'request', service, usage)
        sources = []
        for upload in uploads if use_proformas else []:
            path = storage.save(upload, allow_images=True)
            draft = extract_document(path, upload.filename, 'supplier', service, usage)
            draft['storage_file'] = path.name
            sources.append(prepare_source(draft, default_currency))
        assist_matching(requested['lines'], sources, service, usage)
        with web.SessionLocal() as session:
            for source in sources:
                store_source(session, source)
            unique_sources = {}
            for source in sources:
                identity = source.get('supplier_id') or source['draft']['file_hash']
                if identity not in unique_sources:
                    unique_sources[identity] = source
                    continue
                original = unique_sources[identity]
                known = {line.get('price_record_id') for line in original['draft']['lines']}
                extra = [line for line in source['draft']['lines'] if line.get('price_record_id') not in known]
                if extra:
                    original['draft']['lines'].extend(extra)
                    original['declared_total'] = original['calculated_total'] = original['reconciled'] = None
                    original['draft']['warnings'] += ' Vários documentos do mesmo fornecedor; preços repetidos requerem associação.'
            sources = list(unique_sources.values())
            session.commit()
            preliminary = build_matrix(requested['lines'], sources)
            need_catalog = use_catalog and preliminary['summary']['gaps'] > 0
            catalog = catalog_sources(session, requested['lines'], catalog_days, service, usage) if need_catalog else []
            report = build_matrix(requested['lines'], sources, catalog)
            report['options'] = instruction_options(message)
            report['options']['customer'] = requested.get('customer_name') or next((source['draft'].get('customer_name') for source in sources if source['draft'].get('customer_name')), '')
            if need_catalog and not catalog and report['summary']['gaps']:
                report['alerts'].append(f'Catálogo: nenhum preço elegível para estes artigos no período seleccionado ({catalog_days} dias; 0 = todas as datas).')
            report['message'] = message
            report['request_source'] = requested.get('filename')
            # Compare with older catalogue observations, excluding this document's own rows.
            for row in report['rows']:
                for cell in row['cells']:
                    if not cell or not cell.get('price_record_id'):
                        continue
                    current = session.get(PriceRecord, cell['price_record_id'])
                    product = session.get(SupplierProduct, current.supplier_product_id)
                    if not product.product_id:
                        continue
                    ids = select(SupplierProduct.id).where(SupplierProduct.product_id == product.product_id)
                    history = [p.unit_price for p in session.scalars(select(PriceRecord).where(
                        PriceRecord.supplier_product_id.in_(ids), PriceRecord.id < current.id,
                        PriceRecord.currency == current.currency, PriceRecord.vat_included == current.vat_included,
                        PriceRecord.status == 'accepted')) if p.unit_price]
                    if history and number(cell['price']) > sum(history) / len(history) * number('1.3'):
                        report['alerts'].append(f"Artigo {row['number']}: preço mais de 30% acima da média histórica do catálogo.")
        rid = uuid4()
        if use_online:
            start_online(report, supplier_ids or [])
        save_report(rid, report)
    except (DocumentError, AIServiceError, ValueError) as error:
        return web.templates.TemplateResponse(request=request, name='error.html', context={'error': str(error)},
                                              status_code=422, headers={'X-Chat-Error': '1'})
    finally:
        for upload in uploads + ([solicitation] if solicitation else []):
            upload.file.close()
    if request.headers.get('HX-Request') == 'true':
        return HTMLResponse(f'<article class="edit-card"><h2>Comparação preparada</h2><p>{len(report["rows"])} artigos · {len(report["documents"])} fornecedores</p><a class="send" href="/matrices/{rid.hex}">Abrir matriz e preparar cotação →</a></article>')
    return RedirectResponse(f'/matrices/{rid.hex}', 303)


def start_online(report: dict, selected: list[int]) -> None:
    from app.models import PurchaseRequest, PurchaseRequestItem
    from app.services.search_jobs import search_jobs
    from app.services.comparison_service import normalize
    with web.SessionLocal() as session:
        stores = list(session.scalars(select(Supplier).where(Supplier.active.is_(True), Supplier.scraping_enabled.is_(True), Supplier.website.is_not(None))))
        if selected:
            if len(set(selected)) > 5 or any(sid not in {s.id for s in stores} for sid in selected):
                raise DocumentError('Escolhe até 5 lojas activas com leitura autorizada.')
            stores = [s for s in stores if s.id in selected]
        else:
            descriptions = normalize(' '.join(item['description'] for item in report['items']))
            medical = bool(re.search(r'luva|seringa|sonda|gaze|lamina|hospital|bisturi|lanceta', descriptions))
            technology = bool(re.search(r'computador|portatil|laptop|impressora|monitor|ssd|ram|servidor', descriptions))
            stores = [s for s in stores if any(term in normalize(s.category or '') for term in
                       (('saude', 'medic', 'hospital', 'farmac') if medical else ('tecnolog', 'informatic', 'comput', 'eletron') if technology else ()))]
            stores = stores[:5]
        if not stores:
            report['alerts'].append('Nenhuma loja activa compatível. Podes escolher lojas explicitamente.')
            return
        purchase = PurchaseRequest(title='Pesquisa da matriz', status='draft')
        session.add(purchase)
        session.flush()
        items = []
        for row in report['items']:
            item = PurchaseRequestItem(purchase_request_id=purchase.id, description=row['description'], normalized_description=row['description'],
                                       quantity=number(row.get('quantity')), unit=row.get('unit'), reference=row.get('reference'), specifications_json={})
            session.add(item)
            items.append(item)
        session.flush()
        ids, pid = [item.id for item in items], purchase.id
        session.commit()
        search_jobs.start(pid, ids, [s.id for s in stores], False, web.SessionLocal)
        report['online_request_id'] = pid
        report['online_item_ids'] = ids


@router.get('/matrices/{report_id}')
def show_matrix(request: Request, report_id: UUID):
    report = read_report(report_id)
    return web.templates.TemplateResponse(request=request, name='matrix_page.html', context={'report': report, 'report_id': report_id.hex})


@router.post('/matrices/{report_id}/quote')
async def quote_matrix(request: Request, report_id: UUID):
    report = read_report(report_id)
    form = await request.form()
    options = {key: str(form.get(key, ''))[:1000] for key in ('strategy', 'markup', 'vat', 'company', 'customer', 'tax_id', 'address', 'contacts', 'validity', 'delivery', 'payment', 'exemption')}
    options['manual'] = {str(i): str(form.get(f'manual_{i}', '')) for i in range(len(report['rows']))}
    options['logo'] = report.get('options', {}).get('logo')
    logo = form.get('logo')
    if logo and getattr(logo, 'filename', ''):
        import pymupdf
        if not logo.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            raise HTTPException(422, 'Logótipo: usa PNG ou JPG.')
        data = await logo.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise HTTPException(422, 'Logótipo: máximo 2 MB.')
        try:
            with pymupdf.open(stream=data) as image:
                if image.is_pdf:
                    raise ValueError()
                if image[0].rect.width * image[0].rect.height > 16000000:
                    raise ValueError()
                pix = image[0].get_pixmap()
                if pix.width * pix.height > 16000000:
                    raise ValueError()
                name = uuid4().hex + '.png'
                (settings.upload_path / name).write_bytes(pix.tobytes('png'))
                options['logo'] = name
        except Exception:
            raise HTTPException(422, 'Não foi possível ler o logótipo.') from None
    try:
        quote = calculate_quote(report, options)
    except ValueError as error:
        return HTMLResponse(escape(str(error)), status_code=422)
    report['quote'], report['options'] = quote, options
    save_report(report_id, report)
    if request.headers.get('HX-Request') == 'true':
        return web.templates.TemplateResponse(request=request, name='matrix_quote_summary.html', context={'quote': quote, 'report_id': report_id.hex})
    return RedirectResponse(f'/matrices/{report_id.hex}#quote', 303)


@router.get('/matrices/{report_id}/export.xlsx')
def export_matrix(report_id: UUID):
    return Response(matrix_excel(read_report(report_id)), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename="comparacao.xlsx"'})


@router.get('/matrices/{report_id}/{kind}.pdf')
def export_pdf(report_id: UUID, kind: str):
    if kind not in {'matrix', 'quote'}:
        raise HTTPException(404)
    try:
        content = matrix_pdf(read_report(report_id), customer=kind == 'quote')
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    return Response(content, media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="{kind}.pdf"'})


@router.get('/matrices/{report_id}/online-status')
def online_status(report_id: UUID):
    from app.services.search_jobs import search_jobs
    report = read_report(report_id)
    return search_jobs.status(report['online_request_id']) if report.get('online_request_id') else {'state': 'idle'}


@router.post('/matrices/{report_id}/associate')
def associate_line(report_id: UUID, item_index: Annotated[int, Form(ge=0)],
                   source_index: Annotated[int, Form(ge=0)], line_index: Annotated[int, Form(ge=0)]):
    report = read_report(report_id)
    try:
        item = report['items'][item_index]
        source = report['documents'][source_index]
        line = source['draft']['lines'][line_index]
    except IndexError:
        raise HTTPException(422, 'Artigo ou linha inválida.') from None
    with web.SessionLocal() as session:
        sp = session.get(SupplierProduct, line.get('supplier_product_id')) if line.get('supplier_product_id') else None
        if sp:
            product = session.get(Product, sp.product_id) if sp.product_id else session.scalar(select(Product).where(Product.name == item['description']))
            if not product:
                product = Product(name=item['description'], reference=item.get('reference'), unit=item.get('unit'), specifications_json={}, aliases_json=[])
                session.add(product)
                session.flush()
            aliases = list(product.aliases_json)
            for description in (item['description'], line['description']):
                if description not in aliases:
                    aliases.append(description)
            product.aliases_json = aliases
            sp.product_id = product.id
            price = session.get(PriceRecord, line.get('price_record_id')) if line.get('price_record_id') else None
            if price and price.unit_price and price.currency:
                price.status = 'accepted'
                price.raw_json = dict(price.raw_json, comparison_reviewed=True)
            session.commit()
    line['confidence'] = 100
    source.setdefault('mapping', {})[str(item_index)] = line_index
    source.get('suggestions', {}).pop(str(item_index), None)
    report.update(build_matrix(report['items'], report['documents']))
    report.pop('quote', None)
    save_report(report_id, report)
    return RedirectResponse(f'/matrices/{report_id.hex}', 303)


@router.post('/matrices/{report_id}/online-results')
def online_results(report_id: UUID):
    from app.models import SupplierOffer
    from app.services.search_jobs import search_jobs
    report = read_report(report_id)
    pid = report.get('online_request_id')
    if not pid or search_jobs.status(pid)['state'] in {'running', 'stopping'}:
        raise HTTPException(409, 'A pesquisa ainda não terminou.')
    with web.SessionLocal() as session:
        for offer in session.scalars(select(SupplierOffer).where(SupplierOffer.purchase_request_item_id.in_(report['online_item_ids']))):
            if not offer.unit_price or offer.match_confidence < 85:
                continue
            index = report['online_item_ids'].index(offer.purchase_request_item_id)
            supplier = session.get(Supplier, offer.supplier_id)
            source = next((doc for doc in report['documents'] if doc.get('supplier_id') == supplier.id), None)
            if source is None:
                source = dict(label=supplier.name, supplier_id=supplier.id, draft={'lines': [], 'warnings': '', 'filename': supplier.website}, reconciled=None)
                report['documents'].append(source)
            item = report['items'][index]
            if any(equivalent(item, line) for line in source['draft']['lines']):
                continue
            source['draft']['lines'].append(dict(item, unit_price=str(offer.unit_price), currency=offer.currency if offer.currency != 'UNK' else None,
                vat_included=offer.vat_included, vat_rate=str(offer.vat_rate) if offer.vat_rate is not None else None,
                evidence=offer.product_url, source_kind='website', source_date=offer.observed_at.isoformat()[:10]))
    refreshed = build_matrix(report['items'], report['documents'])
    report.update(refreshed)
    report.pop('quote', None)
    report['online_applied'] = True
    save_report(report_id, report)
    return RedirectResponse(f'/matrices/{report_id.hex}', 303)
