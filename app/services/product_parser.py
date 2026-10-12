import json
import re
from collections.abc import Iterator
from typing import Any
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from html import unescape
from urllib.parse import urljoin, urlsplit
from app.schemas.supplier import website_url
from app.services.gromul_parser import parse_gromul


class ProductPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self.links: list[str] = []
        self.current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attrs = dict(attrs)
        if tag == 'script' and (attrs.get('type') or '').lower() == 'application/ld+json':
            self.current = []
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == 'script' and self.current is not None:
            self.blocks.append(''.join(self.current))
            self.current = None


def objects(value: Any) -> Iterator[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def scalar(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get('name', '')
    return unescape(str(value)) if value is not None else ''


def parse_products(html: str, source_url: str) -> tuple[list[dict], list[str]]:
    page = ProductPage(); page.feed(html)
    products = []
    for block in page.blocks[:50]:
        try:
            nodes = list(objects(json.loads(block)))
        except (ValueError, RecursionError):
            continue
        for node in nodes:
            if 'Product' not in (node.get('@type') if isinstance(node.get('@type'), list) else [node.get('@type')]):
                continue
            name = scalar(node.get('name'))[:300]
            if not name:
                continue
            specs = node.get('additionalProperty', [])
            if isinstance(specs, dict): specs = [specs]
            props = {scalar(p.get('name')): scalar(p.get('value')) for p in specs if isinstance(p, dict)} if isinstance(specs, list) else {}
            offers = node.get('offers', [])
            if isinstance(offers, dict): offers = [offers]
            if not isinstance(offers, list) or not offers: offers = [{}]
            for offer in offers[:10]:
                if not isinstance(offer, dict) or offer.get('@type') == 'AggregateOffer': continue
                price_spec = offer.get('priceSpecification', {})
                if not isinstance(price_spec, dict): price_spec = {}
                raw_price = offer.get('price', price_spec.get('price'))
                price = None
                if re.fullmatch(r'\d+(\.\d{1,2})?', str(raw_price)):
                    try:
                        amount = Decimal(str(raw_price))
                        if 0 < amount < Decimal('10000000000000000'): price = str(amount)
                    except InvalidOperation: pass
                currency = str(offer.get('priceCurrency', price_spec.get('priceCurrency', 'UNK'))).upper()
                if not re.fullmatch('[A-Z]{3}', currency): currency = 'UNK'
                vat = price_spec.get('valueAddedTaxIncluded', offer.get('valueAddedTaxIncluded'))
                if not isinstance(vat, bool): vat = None
                raw_url = offer.get('url', node.get('url', source_url))
                try:
                    product_url = website_url(urljoin(source_url, str(raw_url)))
                    if urlsplit(product_url).hostname != urlsplit(source_url).hostname: continue
                except ValueError: continue
                availability = scalar(offer.get('availability'))
                status = 'Preço público' if price else 'Preço não encontrado'
                if not price and re.search(r'sob consulta|preço por consulta|preco por consulta', scalar(node.get('description')), re.I):
                    status = 'Sob consulta'
                products.append(dict(name=name, brand=scalar(node.get('brand')), model=scalar(node.get('model')),
                    reference=scalar(node.get('mpn', node.get('sku'))), specifications=props,
                    unit=scalar(price_spec.get('unitText', price_spec.get('unitCode'))),
                    price=price, currency=currency, vat=vat, availability=availability or None,
                    url=product_url, status=status, source_url=source_url,
                    condition=scalar(offer.get('itemCondition', node.get('itemCondition'))),
                    restricted=any(k in offer or k in price_spec for k in ('referenceQuantity','eligibleQuantity','eligibleCustomerType','eligibleRegion','validFrom','validThrough','priceValidUntil','billingDuration','billingIncrement','minPrice','maxPrice'))))
    links = []
    for link in page.links:
        try:
            target = website_url(urljoin(source_url, link))
            parts = urlsplit(target)
            product_path = re.search(r'/(product|produto|produtos)/', parts.path)
            if parts.hostname in {'www.querapidoangola.com', 'querapidoangola.com'}:
                product_path = parts.path.startswith('/loja-2/') and len(parts.path.strip('/').split('/')) >= 3
            if parts.hostname == 'tendaplace.ao':
                product_path = re.fullmatch(r'/[^/]+/\d+-[^/]+\.html', parts.path)
            if not parts.query and parts.hostname == urlsplit(source_url).hostname and product_path:
                if target not in links: links.append(target)
        except ValueError:
            continue
    if urlsplit(source_url).hostname == 'store.byod.it.ao':
        # This shop's JSON-LD brand is a category ("Loja HP"). Its
        # explicit product properties contain the actual brand and model.
        for product in products:
            specs = product['specifications']
            product['brand'] = specs.get('Marca', '')
            product['model'] = specs.get('Modelo', '')
        links = [link for link in links if urlsplit(link).path != '/produtos/comparar']
    if not products:
        products = parse_gromul(html, source_url)
    return products[:30], links[:3]
