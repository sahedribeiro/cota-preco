from app.services.product_parser import parse_products

URL = 'https://gromul.ao/produto/portatil-lenovo/'
# Minimal excerpt of the public template observed on 30/09/2026.
HTML = '''<h1 class="wpr-product-title">Portátil Lenovo X1 Yoga 14&quot;</h1>
<div class="wpr-product-price"><span><bdi>485,000.00&nbsp;<span>Kz</span></bdi></span></div>
<span class="posted_in">Categoria: <a>Recondicionados</a></span>
<span class="posted_in">Marca: <a>Lenovo</a></span>
<div class="wpr-product-stock"><p class="in-stock">Em stock</p></div>'''


def test_gromul_price_and_refurbished_condition():
    product = parse_products(HTML, URL)[0][0]
    assert product['price'] == '485000.00' and product['brand'] == 'Lenovo'
    assert product['condition'] == 'RefurbishedCondition'
    assert product['vat'] is None and product['unit'] == ''
    assert product['availability'] == 'InStock'
    assert parse_products(HTML, 'https://example.com/produto/a')[0] == []
    assert parse_products(HTML.replace('485,000.00', '485.000,00'), URL)[0][0]['price'] is None
    assert parse_products(HTML.replace('485,000.00', '500,000.00 485,000.00'), URL)[0][0]['price'] is None


def test_product_discovery_never_follows_cart_actions():
    links = ''.join(f'<a href="{URL}{suffix}">Produto</a>' for suffix in ['?add-to-cart=1', '?quantity=5', ''])
    assert parse_products(links, 'https://gromul.ao/?s=Lenovo')[1] == [URL]
