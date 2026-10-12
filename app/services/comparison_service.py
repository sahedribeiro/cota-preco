import re
import unicodedata
from app.models import PurchaseRequestItem, SupplierOffer


def normalize(value: str) -> str:
    text = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def normalize_spec(value: str) -> str:
    return re.sub(r'(\d)([a-z])', r'\1 \2', normalize(value))


def usable_reference(item: PurchaseRequestItem) -> bool:
    reference = normalize(item.reference or '')
    return bool(reference and reference not in {normalize(item.brand or ''), normalize(item.model or '')})


def match_product(item: PurchaseRequestItem, product: dict) -> tuple[int, list[str]]:
    missing = []
    for field in ('brand', 'model', 'reference'):
        wanted = getattr(item, field)
        if field == 'reference' and wanted and not usable_reference(item):
            missing.append('Referência incompleta: repete a marca ou modelo; confirmar o código exacto')
            continue
        found = product.get(field)
        if wanted and found and normalize(wanted) != normalize(found):
            return 0, [f'{field}: valor diferente']
        if wanted and not found: missing.append(f'{field}: não confirmado')
    found_specs = {normalize(k): normalize_spec(v) for k,v in product.get('specifications', {}).items()}
    for key, value in (item.specifications_json or {}).items():
        actual = found_specs.get(normalize(key))
        wanted = normalize_spec(value)
        if actual and actual != wanted:
            if actual.startswith(wanted + ' '):
                missing.append(f'{key}: confirmar os detalhes adicionais da fonte')
            else:
                return 0, [f'{key}: especificação diferente']
        if not actual: missing.append(f'{key}: não confirmado')
    wanted_unit = normalize(item.unit or '')
    found_unit = normalize(product.get('unit', ''))
    units = {'unidade', 'unidades', 'un', 'c62', 'piece', 'ea'}
    if wanted_unit and found_unit and wanted_unit != found_unit and not (wanted_unit in units and found_unit in units):
        return 0, ['Unidade diferente']
    if not wanted_unit or not found_unit: missing.append('Unidade não confirmada')
    title = normalize(item.normalized_description or item.description)
    exact = title == normalize(product['name'])
    reference = bool(usable_reference(item) and normalize(item.reference) == normalize(product.get('reference', '')))
    model = bool(item.model and item.brand and normalize(item.model) == normalize(product.get('model', '')) and normalize(item.brand) == normalize(product.get('brand', '')))
    if not (exact or reference or model):
        return 50 if set(title.split()) & set(normalize(product['name']).split()) else 0, ['Identidade do produto requer validação'] + missing
    return (74, missing) if missing else (98 if reference else 95, ['Identidade e características pedidas confirmadas'])


def comparison_values(offer: SupplierOffer) -> dict:
    history = offer.review_history_json or []
    review = history[-1] if history else None
    if review and review['action'] == 'save':
        return {
            'matched': review['match'] == 'yes' and review['unit'] == 'yes',
            'stock': review['stock'] == 'in', 'new': review['condition'] == 'new',
            'vat': {'included': True, 'excluded': False}.get(review['vat']),
        }
    return {
        'matched': offer.match_confidence >= 90,
        'stock': offer.availability in ('https://schema.org/InStock', 'http://schema.org/InStock', 'InStock'),
        'new': offer.evidence_json.get('condition') in ('https://schema.org/NewCondition', 'http://schema.org/NewCondition', 'NewCondition'),
        'vat': offer.vat_included,
    }


def cheapest_per_item(offers: list[SupplierOffer], approved_documents: set[int] | None = None) -> tuple[SupplierOffer | None, str]:
    approved_documents = approved_documents or set()
    eligible = [o for o in offers if comparison_values(o)['matched'] and o.unit_price is not None and o.unit_price > 0
                and (o.id in approved_documents if o.document_line_key else not o.evidence_json.get('restricted')) and o.currency != 'UNK'
                and comparison_values(o)['stock'] and comparison_values(o)['new']]
    if not eligible:
        return None, 'Sem oferta suficientemente confirmada para escolha automática (confiança, stock, unidade ou estado do produto).'
    if any(comparison_values(o)['vat'] is None for o in eligible) or len({(o.currency, comparison_values(o)['vat']) for o in eligible}) != 1:
        return None, 'Moeda ou tratamento de IVA não comparáveis. Revê as ofertas; não foi escolhido um vencedor.'
    return min(eligible, key=lambda o: o.unit_price), 'Menor preço unitário entre ofertas comparáveis. Transporte não incluído na comparação; stock e quantidade devem ser confirmados.'
