"""Local extraction first; structured AI only for layouts we cannot read safely."""
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from zipfile import ZipFile, BadZipFile

import pymupdf
from openpyxl import load_workbook

from app.schemas.comparison_document import ComparisonDocument
from app.services.catalog_documents import document_blocks, text_blocks
from app.services.file_storage_service import DocumentError
from app.services.comparison_service import normalize


def blank_document() -> dict:
    return dict(supplier_name=None, customer_name=None, document_reference=None,
                document_date=None, valid_until=None, document_total=None,
                angola_evidence=None, warnings='', lines=[])


def decimal_text(text: str) -> str:
    return text.replace(' ', '').replace('\xa0', '').replace(',', '.')


def table_text(path: Path) -> str:
    if path.suffix == '.csv':
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp1252')
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        return '\n'.join('\t'.join(row) for row in csv.reader(io.StringIO(text), dialect))
    with ZipFile(path) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 30 * 1024 * 1024:
            raise DocumentError('XLSX demasiado grande depois de descomprimido.')
    book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        return '\n'.join('\t'.join('' if value is None else str(value) for value in row)
                         for sheet in book for row in sheet.values if any(value is not None for value in row))
    finally:
        book.close()


def request_table(text: str) -> dict | None:
    rows = [line.split('\t') for line in text.splitlines()]
    header = None
    result = blank_document()
    for row in rows:
        names = [normalize(cell).replace(' ', '') for cell in row]
        if any(name in {'descricao', 'description', 'produto', 'artigo'} for name in names):
            header = names
            continue
        if not header:
            continue
        values = dict(zip(header, row))
        description = next((values[k].strip() for k in ('descricao', 'description', 'produto', 'artigo') if values.get(k)), '')
        quantity = next((values[k] for k in ('qtde', 'qtd', 'quantidade', 'quantity') if values.get(k)), None)
        ordinal = values.get('ord') or values.get('n')
        if not description or (not quantity and not (ordinal and ordinal.strip().isdigit())):
            continue
        result['lines'].append(dict(number=values.get('ord') or values.get('n') or str(len(result['lines']) + 1),
            description=description, quantity=decimal_text(quantity) if quantity else None, unit=values.get('uni') or values.get('unidade') or values.get('un'),
            reference=values.get('referencia'), unit_price=None, line_total=None, currency=None,
            vat_included=None, vat_rate=None, evidence=' | '.join(row), confidence=100))
    return result if result['lines'] else None


def aligned_proforma(text: str) -> dict | None:
    """Accept only an explicit numbered quantity/unit-price/total table."""
    if not re.search(r'Pre[çc]o\s+Unit', text, re.I):
        return None
    result = blank_document()
    pattern = r'^\s*(\d+)\s+(.+?)\s+(UNI|UN|UND|CX)\s+(\d+(?:[,.]\d+)?)\s+([\d ]+,\d{2})\s+([\d ]+,\d{2})\s*$'
    unmatched = []
    for text_line in text.splitlines():
        match = re.match(pattern, text_line)
        if match:
            number, description, unit, quantity, price, total = match.groups()
            result['lines'].append(dict(number=number, description=description.strip(), reference=None,
                unit=unit, quantity=decimal_text(quantity), unit_price=decimal_text(price), line_total=decimal_text(total),
                currency=None, vat_included=None, vat_rate=None, evidence=text_line.strip(), confidence=100))
        elif re.match(r'^\s*\d+\s+\S', text_line):
            unmatched.append(text_line)
    if not result['lines'] or unmatched:
        return None
    header = text.split('Ord')[0]
    beneficiary = re.search(r'Benefici[áa]rio:\s*(.+)', text, re.I)
    if beneficiary:
        result['supplier_name'] = beneficiary[1].strip()
    else:
        names = [line.strip() for line in header.splitlines()
                 if re.fullmatch(r'[A-ZÁÉÍÓÚÃÕÇ][A-ZÁÉÍÓÚÃÕÇ &.-]{2,100}', line.strip())]
        if len(names) == 1:
            result['supplier_name'] = names[0]
    for field, pattern in (
        ('document_reference', r'proforma\s+N[º°o]?\s*(\S+)'),
        ('customer_name', r'Cliente\s+([^\n]+)'),
        ('document_date', r'Data de Emiss[ãa]o:\s*(\S+)'),
        ('document_total', r'^\s*Total\s+([\d ]+,\d{2})\s*$'),
    ):
        match = re.search(pattern, text, re.I | re.M)
        if match:
            result[field] = decimal_text(match[1]) if field == 'document_total' else match[1].strip()
    evidence = re.search(r'\bAO\d{2}\s*\d+|\bAngola\b|\bLuanda\b|\bKz\b|Contribuinte:\s*\d{10}', text, re.I)
    if evidence:
        result['angola_evidence'] = evidence[0]
    return result


def extract_document(path: Path, filename: str, role: str, service, record_usage) -> dict:
    try:
        return _extract_document(path, filename, role, service, record_usage)
    except (OSError, ValueError, RuntimeError, BadZipFile):
        raise DocumentError(f'Não foi possível ler {filename}. Confirma o formato e se o ficheiro abre correctamente.') from None


def _extract_document(path: Path, filename: str, role: str, service, record_usage) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cache = path.parent / f'{digest}.{role}.extraction-v2.json'
    if cache.exists():
        cached = json.loads(cache.read_text(encoding='utf-8'))
        cached['filename'] = filename
        return cached
    text = ''
    mixed_pdf = False
    if path.suffix in {'.xlsx', '.csv'}:
        text = table_text(path)
    elif path.suffix == '.pdf':
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                raise DocumentError('Documento protegido por palavra-passe.')
            pages = [page.get_text(sort=True) for page in doc]
            mixed_pdf = any(not page.strip() for page in pages)
            text = '\n'.join(pages)
    local = request_table(text) if role == 'request' and path.suffix in {'.xlsx', '.csv'} else aligned_proforma(text) if role == 'supplier' else None
    if mixed_pdf:
        local = None
    if local and role == 'supplier' and not local['supplier_name']:
        local = None
    result = local or blank_document()
    if not local:
        raw_blocks = ((text, None),) if text.strip() and not mixed_pdf else document_blocks(path)
        blocks = ((part, image) for block, image in raw_blocks for part in ([block] if image else text_blocks(block, 2200)))
        for index, (block, image) in enumerate(blocks):
            checkpoint = path.parent / f'{digest}.{role}.{index}.block-v2.json'
            if checkpoint.exists():
                part = json.loads(checkpoint.read_text(encoding='utf-8'))
            else:
                response = service.extract_comparison_block(block, filename, role, image)
                record_usage(response)
                part = response.response.model_dump()
                checkpoint.write_text(json.dumps(part, ensure_ascii=False), encoding='utf-8')
            result['lines'].extend(part.pop('lines'))
            for key, value in part.items():
                if key == 'warnings' and value:
                    result[key] = (result.get(key, '') + '\n' + value).strip()
                elif value and not result.get(key):
                    result[key] = value
                elif value and key in {'supplier_name', 'document_reference', 'document_date', 'valid_until'} and normalize(str(result[key])) != normalize(str(value)):
                    result['conflict'] = True
                    result['warnings'] += f'\nDados divergentes entre blocos: {key}. Confirmar antes de utilizar os preços.'
    if not result['lines']:
        raise DocumentError(f'Não foram encontrados artigos em {filename}.')
    if result.get('conflict'):
        for line in result['lines']:
            line['confidence'] = 0
    if role == 'request':
        result['supplier_name'] = None
        for line in result['lines']:
            line['unit_price'] = line['line_total'] = None
    result['file_hash'] = digest
    result['filename'] = filename
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result
