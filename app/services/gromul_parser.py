"""Read only the product fields in Gromul's public HTML template."""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit


class GromulProduct(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, str | None]] = []
        self.fields: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attrs = dict(attrs)
        classes = (attrs.get('class') or '').split()
        key = None
        for expected_tag, css, field in [('h1', 'wpr-product-title', 'name'), ('div', 'wpr-product-price', 'price'),
                                          ('span', 'posted_in', 'meta'), ('div', 'wpr-product-stock', 'stock')]:
            if tag == expected_tag and css in classes:
                key = field
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append((tag, key))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if any(tag in {'script', 'style'} for tag, _ in self.stack):
            return
        for _, key in self.stack:
            if key:
                self.fields.setdefault(key, []).append(data)


def parse_gromul(html: str, url: str) -> list[dict]:
    if urlsplit(url).hostname != 'gromul.ao' or not urlsplit(url).path.startswith('/produto/'):
        return []
    parser = GromulProduct()
    parser.feed(html)
    fields = {k: ' '.join(' '.join(v).split()) for k, v in parser.fields.items()}
    if not fields.get('name'):
        return []
    # This template publishes 485,000.00 Kz, not the Portuguese separators.
    match = re.fullmatch(r'(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})\s*Kz', fields.get('price', ''))
    price = (match[1].replace(',', '') + '.' + match[2]) if match else None
    brand = re.search(r'Marca:\s*(.+)$', fields.get('meta', ''))
    stock = fields.get('stock', '').casefold()
    return [dict(name=fields['name'][:300], brand=brand[1] if brand else '', model='', reference='', specifications={},
                 unit='', price=price, currency='AOA', vat=None,
                 availability='InStock' if stock == 'em stock' else 'OutOfStock' if stock == 'esgotado' else None,
                 url=url, source_url=url, status='Preço público' if price else 'Preço não confirmado',
                 condition='RefurbishedCondition' if 'Recondicionados' in fields.get('meta', '') else '',
                 restricted=False, adapter='gromul-html', published_fields=fields)]
