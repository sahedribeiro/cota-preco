"""One row per requested item; Decimal calculations and source provenance."""
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
from statistics import median
from difflib import SequenceMatcher

from sqlalchemy import select
from app.core.formatting import money
from app.models import Supplier, SupplierProduct, PriceRecord, Product, CatalogImport, utc_now
from app.services.catalog_service import add_price, current_price
from app.services.product_wording import canonical_description, canonical_unit
from app.services.comparison_service import normalize


def number(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and 0 <= result < Decimal('1e16') else None
    except (ValueError, InvalidOperation):
        return None


def rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def equivalent(item: dict, line: dict) -> bool:
    a, b = canonical_unit(item.get('unit')), canonical_unit(line.get('unit'))
    if a and b and a != b:
        return False
    ra, rb = normalize(item.get('reference') or ''), normalize(line.get('reference') or '')
    if ra and rb and ra != rb:
        return False
    # References alone must not override explicit differences in specifications.
    da, db = canonical_description(item['description']), canonical_description(line['description'])
    return da == db or (ra and ra == rb and re.findall(r'\d+(?:[.,]\d+)?', da) == re.findall(r'\d+(?:[.,]\d+)?', db))


def assist_matching(items: list[dict], sources: list[dict], service, record_usage) -> None:
    for source in sources:
        pending = []
        lines = source['draft']['lines']
        source.setdefault('mapping', {})
        source.setdefault('suggestions', {})
        for index, item in enumerate(items):
            if any(equivalent(item, line) for line in lines):
                continue
            ranked = []
            for li, line in enumerate(lines):
                a, b = canonical_unit(item.get('unit')), canonical_unit(line.get('unit'))
                if a and b and a != b:
                    continue
                ra, rb = normalize(item.get('reference') or ''), normalize(line.get('reference') or '')
                if ra and rb and ra != rb:
                    continue
                da, db = normalize(item['description']), normalize(line['description'])
                if re.findall(r'\d+(?:[.,]\d+)?', da) != re.findall(r'\d+(?:[.,]\d+)?', db):
                    continue
                score = SequenceMatcher(None, da, db).ratio()
                if score >= .45:
                    ranked.append((score, li, line))
            choices = [{'line_index': li, 'description': line['description'], 'unit': line.get('unit'), 'reference': line.get('reference')}
                       for _, li, line in sorted(ranked, reverse=True)[:5]]
            if choices:
                pending.append(dict(item_index=index, description=item['description'], unit=item.get('unit'), reference=item.get('reference'), candidates=choices))
        for offset in range(0, len(pending), 8):
            batch = pending[offset:offset+8]
            response = service.match_comparison_lines(batch)
            record_usage(response)
            allowed = {candidate['item_index']: {line['line_index'] for line in candidate['candidates']} for candidate in batch}
            for match in response.response.matches:
                if match.item_index not in allowed or match.line_index not in allowed[match.item_index]:
                    continue
                if match.confidence >= 85:
                    source['mapping'][str(match.item_index)] = match.line_index
                elif match.confidence >= 60:
                    source['suggestions'][str(match.item_index)] = dict(line_index=match.line_index, reason=match.reason)


def prepare_source(draft: dict, default_currency: str = 'AOA') -> dict:
    for line in draft['lines']:
        if not line.get('currency') and (draft.get('angola_evidence') or default_currency):
            line['currency'] = 'AOA' if draft.get('angola_evidence') else default_currency
            line['currency_assumed'] = True
        line['source_kind'] = 'proforma'
        line['source_date'] = draft.get('document_date')
    lines = draft['lines']
    values = [(number(line.get('quantity')), number(line.get('unit_price'))) for line in lines]
    complete = all(q is not None and p is not None for q, p in values)
    total = sum((rounded(q * p) for q, p in values if q is not None and p is not None), Decimal(0))
    declared = number(draft.get('document_total'))
    mismatch = [line.get('number') or str(i+1) for i, line in enumerate(lines)
                if number(line.get('line_total')) is not None and number(line.get('quantity')) is not None
                and number(line.get('unit_price')) is not None
                and abs(number(line['quantity']) * number(line['unit_price']) - number(line['line_total'])) > 1]
    return dict(label=draft.get('supplier_name') or 'Fornecedor por identificar', draft=draft,
                calculated_total=str(total), declared_total=str(declared) if declared is not None else None,
                reconciled=(complete and abs(total-declared) <= len(lines) and not mismatch) if declared is not None else None,
                line_mismatches=mismatch)


def store_source(session, source: dict, supplier_id: int | None = None) -> None:
    draft = source['draft']
    name = draft.get('supplier_name')
    if not name:
        pending = session.scalar(select(CatalogImport).where(CatalogImport.draft_json['file_hash'].as_string() == draft['file_hash']))
        if pending is None:
            pending = CatalogImport(filename=draft.get('storage_file', draft['filename']), source_kind='proforma',
                                    draft_json=dict(draft, extracted=True), status='pending')
            session.add(pending)
            session.flush()
        source['pending_import_id'] = pending.id
        for line in draft['lines']:
            line['confidence'] = min(line.get('confidence', 100), 84)
        return
    supplier = session.get(Supplier, supplier_id) if supplier_id else next((s for s in session.scalars(select(Supplier)) if normalize(s.name) == normalize(name)), None)
    if supplier is None:
        supplier = Supplier(name=name, country='Angola' if draft.get('angola_evidence') else None)
        session.add(supplier)
        session.flush()
    source['supplier_id'] = supplier.id
    identity = str(supplier.id) + '|' + (draft.get('document_reference') or draft['file_hash'])
    prefix = hashlib.sha256(identity.encode()).hexdigest()
    for index, line in enumerate(draft['lines']):
        if draft.get('conflict'):
            line['confidence'] = 0
        origin = f'comparison:{prefix}:{index}'
        existing = session.scalar(select(PriceRecord).where(PriceRecord.origin_key == origin))
        fields = ('description', 'reference', 'unit', 'unit_price', 'currency', 'vat_included', 'vat_rate', 'price_date', 'valid_until')
        values = {key: line.get(key) for key in fields}
        if existing and values != {key: existing.raw_json.get(key) for key in fields}:
            # A revised document must preserve the previous observation, not silently reuse its price.
            version = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:20]
            origin += ':' + version
            line['confidence'] = 0
            warning = 'Documento com a mesma referência e dados alterados: confirmar esta versão.'
            if warning not in draft.get('warnings', ''):
                draft['warnings'] = (draft.get('warnings', '') + '\n' + warning).strip()
        record = add_price(session, supplier.id, line, origin, 'proforma',
                           draft['filename'], line.get('price_date') or draft.get('document_date'), line.get('valid_until') or draft.get('valid_until'))
        sp = session.get(SupplierProduct, record.supplier_product_id)
        if not sp.product_id:
            canonical = next((p for p in session.scalars(select(Product)) if equivalent(line, {'description': p.name, 'unit': p.unit, 'reference': p.reference})), None)
            if canonical is None:
                canonical = Product(name=line['description'], reference=line.get('reference'), unit=line.get('unit'), specifications_json={}, aliases_json=[])
                session.add(canonical)
                session.flush()
            sp.product_id = canonical.id
        line['price_record_id'] = record.id
        line['supplier_product_id'] = record.supplier_product_id


def catalog_sources(session, items: list[dict], days: int, service=None, record_usage=None) -> list[dict]:
    docs = {}
    candidates = {}
    products = {p.id: p for p in session.scalars(select(Product))}
    for sp in session.scalars(select(SupplierProduct)):
        canonical = products.get(sp.product_id)
        record = current_price(list(session.scalars(select(PriceRecord).where(PriceRecord.supplier_product_id == sp.id))))
        if not record or not record.unit_price:
            continue
        date = record.price_date
        if days and (not date or date.replace(tzinfo=timezone.utc) < utc_now() - timedelta(days=days)):
            continue
        base = dict(description=sp.description, reference=sp.reference, unit=sp.unit)
        aliases = ([canonical.name] + canonical.aliases_json) if canonical else []
        matches = [i for i, item in enumerate(items) if equivalent(item, base) or
                   any(equivalent(item, base | {'description': alias}) for alias in aliases)]
        supplier = session.get(Supplier, sp.supplier_id)
        candidate = candidates.setdefault(sp.supplier_id, dict(label=supplier.name, supplier_id=sp.supplier_id,
            calculated_total=None, declared_total=None, reconciled=None, line_mismatches=[],
            draft={'lines': [], 'warnings': '', 'filename': 'Catálogo interno'}))
        candidate['draft']['lines'].append(base | dict(unit_price=str(record.unit_price), quantity=None,
            currency=record.currency, vat_included=record.vat_included,
            vat_rate=str(record.vat_rate) if record.vat_rate is not None else None, evidence=record.evidence,
            source_kind='catalog', source_date=date.isoformat()[:10] if date else None,
            price_record_id=record.id, supplier_product_id=sp.id, confidence=100))
        if not matches:
            continue
        doc = docs.setdefault(sp.supplier_id, dict(label=supplier.name, supplier_id=sp.supplier_id,
            calculated_total=None, declared_total=None, reconciled=None, line_mismatches=[],
            draft={'lines': [], 'warnings': '', 'filename': 'Catálogo interno'}))
        for index in matches:
            doc['draft']['lines'].append(base | dict(item_index=index, unit_price=str(record.unit_price), quantity=None,
                currency=record.currency, vat_included=record.vat_included,
                vat_rate=str(record.vat_rate) if record.vat_rate is not None else None, evidence=record.evidence,
                source_kind='catalog', source_date=date.isoformat()[:10] if date else None,
                price_record_id=record.id, supplier_product_id=sp.id, confidence=100))
    if service:
        for sid, candidate in candidates.items():
            known = {line['item_index'] for line in docs.get(sid, {}).get('draft', {}).get('lines', [])}
            # Reuse confirmed aliases before asking AI; keep original item indices.
            candidate['mapping'] = {}
            candidates_to_match = [dict(item, description='') if index in known else item for index, item in enumerate(items)]
            assist_matching(candidates_to_match, [candidate], service, record_usage)
            for index, line_index in candidate.get('mapping', {}).items():
                if int(index) in known:
                    continue
                target = docs.setdefault(sid, {key: value for key, value in candidate.items() if key not in {'draft', 'mapping', 'suggestions'}} | {'draft': {'lines': [], 'warnings': '', 'filename': 'Catálogo interno'}})
                target['draft']['lines'].append(dict(candidate['draft']['lines'][line_index], item_index=int(index)))
    return list(docs.values())


def row_values(row: dict) -> None:
    offers = [cell for cell in row['cells'] if cell and cell.get('price') is not None]
    currencies = {cell.get('currency') for cell in offers}
    counts = {currency: sum(cell.get('currency') == currency for cell in offers) for currency in currencies if currency}
    row['currency'] = max(counts, key=lambda currency: (counts[currency], currency == 'AOA', currency)) if counts else None
    row['comparison_warning'] = None
    comparable_offers = [cell for cell in offers if cell.get('currency') == row['currency']]
    mixed_vat = {cell.get('vat_included') for cell in comparable_offers} >= {True, False}
    if mixed_vat and any(cell.get('vat_included') is False and number(cell.get('vat_rate')) is None for cell in comparable_offers):
        row['comparison_warning'] = 'IVA incluído/excluído: falta taxa para normalizar.'
    if not row['currency']:
        row['comparison_warning'] = 'Moeda não indicada; confirma a moeda.'
    if len(currencies) > 1:
        row['issues'].append('Moedas diferentes: menor/maior apenas em ' + (row['currency'] or 'moeda confirmada') + '.')
    for cell in offers:
        price = number(cell['price'])
        if cell in comparable_offers and mixed_vat and cell.get('vat_included') is False and number(cell.get('vat_rate')) is not None:
            price = rounded(price * (1 + number(cell['vat_rate']) / 100))
        cell['compared_price'] = str(price)
    values = [number(cell['compared_price']) for cell in comparable_offers]
    row['count'] = len(offers)
    comparable = len(values) >= 2 and not row['comparison_warning']
    row['lowest'] = str(min(values)) if comparable else None
    row['highest'] = str(max(values)) if comparable else None
    row['difference_percent'] = str(rounded((max(values) / min(values) - 1) * 100)) if comparable and min(values) > 0 else None


def proportional_alerts(rows: list[dict], docs: list[dict]) -> list[str]:
    alerts = []
    for a in range(len(docs)):
        for b in range(a):
            pairs = [(number(row['cells'][a]['price']), number(row['cells'][b]['price'])) for row in rows
                     if row['cells'][a] and row['cells'][b] and row['cells'][a].get('price')
                     and row['cells'][b].get('price') and row['cells'][a]['currency'] == row['cells'][b]['currency']]
            if len(pairs) < 3:
                continue
            factor = median([x / y for x, y in pairs if y > 0])
            count = sum(abs(x / y / factor - 1) <= Decimal('.002') for x, y in pairs if y > 0)
            if count < max(3, len(rows) * Decimal('.8')):
                continue
            left, right = docs[a]['label'], docs[b]['label']
            if factor < 1:
                left, right, factor = right, left, 1 / factor
            alerts.append(f'Preços de {left} são {right} × {factor:.3f} em {count}/{len(rows)} itens: padrão compatível com propostas de cobertura; não constitui prova de concertação.')
    return alerts


def build_matrix(items: list[dict], sources: list[dict], catalog: list[dict] | None = None) -> dict:
    docs = list(sources)
    rows = [dict(number=item.get('number') or str(i+1), product=item['description'], unit=item.get('unit'),
                 reference=item.get('reference'), quantity=item.get('quantity'), cells=[],
                 issues=[] if number(item.get('quantity')) else ['Quantidade por confirmar.'])
            for i, item in enumerate(items)]
    for source in sources:
        for index, (item, row) in enumerate(zip(items, rows)):
            mapped = source.get('mapping', {}).get(str(index))
            matches = [source['draft']['lines'][mapped]] if mapped is not None else [line for line in source['draft']['lines'] if line.get('item_index') == index or equivalent(item, line)]
            cell = None
            if len(matches) == 1:
                line = matches[0]
                price = number(line.get('unit_price'))
                cell = dict(line, price=str(price) if price is not None and price > 0 else None)
                if line.get('confidence', 100) < 85:
                    row['issues'].append(source['label'] + ': confirmar preço extraído com baixa confiança.')
                if number(line.get('quantity')) != number(item.get('quantity')) and line.get('source_kind') != 'catalog':
                    row['issues'].append(source['label'] + ': quantidade cotada diferente da solicitação.')
            elif len(matches) > 1:
                row['issues'].append(source['label'] + ': várias linhas possíveis; requer associação.')
            if str(index) in source.get('suggestions', {}):
                row['issues'].append(source['label'] + ': associação sugerida para confirmar.')
            row['cells'].append(cell)
    for source in catalog or []:
        existing = next((i for i, doc in enumerate(docs) if doc.get('supplier_id') == source.get('supplier_id')), None)
        additions = {}
        for line in source['draft']['lines']:
            index = line['item_index']
            row = rows[index]
            if sum(bool(cell and cell.get('price')) for cell in row['cells']) >= 3:
                continue
            if existing is not None and row['cells'][existing]:
                continue
            # Repeated catalogue aliases for one supplier: choose most recent date/id.
            if index not in additions or (line.get('source_date') or '', line['price_record_id']) > (additions[index].get('source_date') or '', additions[index]['price_record_id']):
                additions[index] = dict(line, price=line['unit_price'])
        if not additions:
            continue
        if existing is None:
            existing = len(docs)
            docs.append(source)
            for row in rows:
                row['cells'].append(None)
        for index, line in additions.items():
            rows[index]['cells'][existing] = line
    for row in rows:
        row_values(row)
    currencies = {row['currency'] for row in rows if row['count']}
    single_currency = len(currencies) == 1 and None not in currencies
    totals = []
    for index, doc in enumerate(docs):
        cells = [(row, row['cells'][index]) for row in rows if row['cells'][index] and row['cells'][index].get('price')]
        valid = single_currency and all(number(row['quantity']) is not None and cell['currency'] == row['currency'] for row, cell in cells)
        total = sum((rounded(number(row['quantity']) * number(cell['compared_price'])) for row, cell in cells), Decimal(0)) if valid else None
        totals.append(str(total) if total is not None and cells else None)
        doc['covered'] = len(cells)
    ranked = sorted({number(total) for total, doc in zip(totals, docs) if total is not None and doc['covered'] == len(rows)})
    for total, doc in zip(totals, docs):
        doc['rank'] = ranked.index(number(total)) + 1 if total is not None and doc['covered'] == len(rows) else None
    complete = single_currency and all(row['lowest'] and number(row['quantity']) for row in rows)
    low = sum((rounded(number(row['quantity']) * number(row['lowest'])) for row in rows), Decimal(0)) if complete else None
    high = sum((rounded(number(row['quantity']) * number(row['highest'])) for row in rows), Decimal(0)) if complete else None
    alerts = proportional_alerts(rows, docs)
    for doc in docs:
        if doc.get('reconciled') is False:
            alerts.append(f"{doc['label']}: total calculado {money(doc['calculated_total'])}; declarado {money(doc['declared_total'])}. Linhas fora da tolerância: {', '.join(doc.get('line_mismatches', [])) or 'total geral'}. Verifica extracção e impostos/descontos.")
    return dict(version=2, items=items, documents=docs, rows=rows, totals=totals, alerts=alerts,
        summary=dict(items=len(rows), suppliers=len(docs), three_prices=sum(row['count'] >= 3 for row in rows),
            gaps=sum(row['count'] < 3 for row in rows), review=sum(bool(row['issues'] or row['comparison_warning']) for row in rows),
            lowest=str(low) if low is not None else None, highest=str(high) if high is not None else None,
            difference=str(high-low) if low is not None else None,
            difference_percent=str(rounded((high/low-1)*100)) if low else None,
            currency=next(iter(currencies)) if single_currency else None))
