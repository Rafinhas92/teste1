"""Local development server and Mercado Pago Checkout Pro integration."""
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from decimal import Decimal
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'restaurant-config.js').read_text().split('window.restaurantConfig = ', 1)[1].strip().rstrip(';'))
CATALOG = {item['id']: item for item in CONFIG['menu']}
DATA = Path(os.environ.get('ORDER_DATA_DIR', ROOT / '.local'))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'orders.sqlite3'
MAPS_KEY = os.environ.get('ORS_API_KEY', '')
ORS_ORIGIN = None
TOKEN = os.environ.get('MERCADO_PAGO_ACCESS_TOKEN', '')
PUBLIC_URL = os.environ.get('PAYMENT_PUBLIC_URL', '').rstrip('/')
TEST_MODE = os.environ.get('PAYMENT_TEST_MODE', 'true').lower() == 'true'
LOCK = threading.Lock()


def db():
    connection = sqlite3.connect(DB, timeout=20)
    connection.row_factory = sqlite3.Row
    return connection


with db() as connection:
    connection.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, request_key TEXT UNIQUE, fingerprint TEXT, payload TEXT, total INTEGER, status TEXT, checkout_url TEXT, created REAL)')
    connection.execute('CREATE TABLE IF NOT EXISTS delivery_quotes (id TEXT PRIMARY KEY, address TEXT, meters INTEGER, fee INTEGER, expires REAL)')
os.chmod(DB, 0o600)


def ready():
    parsed = urlsplit(PUBLIC_URL)
    return bool(TOKEN and parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.query and not parsed.fragment)


def mp(path, payload=None, key=None):
    headers = {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}
    if key:
        headers['X-Idempotency-Key'] = key
    request = Request('https://api.mercadopago.com' + path, headers=headers,
                      data=None if payload is None else json.dumps(payload).encode())
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def delivery_fee(meters):
    if type(meters) is not int or meters < 0:
        raise ValueError('Distância inválida.')
    return 650 if meters <= 3000 else 750 if meters <= 5000 else 850 if meters <= 7000 else 1000


def address_key(address):
    if not isinstance(address, str) or not 15 <= len(address.strip()) <= 300:
        raise ValueError('Informe rua, número, bairro, cidade e CEP.')
    return ' '.join(address.strip().casefold().split())


def ors_request(path, payload=None):
    if not MAPS_KEY:
        raise ValueError('Cálculo de frete ainda não ativado. Combine a entrega pelo WhatsApp.')
    request = Request('https://api.openrouteservice.org' + path,
        headers={'Content-Type': 'application/json', 'Authorization': MAPS_KEY},
        data=None if payload is None else json.dumps(payload).encode())
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 429:
            raise ValueError('O limite de consultas de frete foi atingido. Tente depois ou combine a entrega pelo WhatsApp.') from None
        if error.code in (401, 403):
            raise ValueError('O serviço de frete precisa de configuração. Combine a entrega pelo WhatsApp.') from None
        if error.code in (400, 404):
            raise ValueError('Não encontramos uma rota para esse endereço. Confira os dados ou fale com o restaurante.') from None
        raise


def geocode_address(address):
    result = ors_request('/geocode/search?' + urlencode({'text': address, 'boundary.country': 'BR', 'layers': 'address', 'size': 3}))
    candidates = []
    for feature in result.get('features', []):
        properties = feature.get('properties', {})
        coordinates = feature.get('geometry', {}).get('coordinates', [])
        number = str(properties.get('housenumber', ''))
        confidence = properties.get('confidence', 0)
        if (properties.get('layer') == 'address' and number
            and re.search(r'(?<!\d)' + re.escape(number) + r'(?!\d)', address, re.IGNORECASE)
            and isinstance(confidence, (int, float)) and confidence >= 0.8
            and len(coordinates) == 2
            and all(type(value) in (int, float) and math.isfinite(value) for value in coordinates)
            and -180 <= coordinates[0] <= 180 and -90 <= coordinates[1] <= 90):
            candidates.append((confidence, coordinates))
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    if not candidates or (len(candidates) > 1 and candidates[0][0] == candidates[1][0] and candidates[0][1] != candidates[1][1]):
        raise ValueError('Não foi possível localizar o endereço com precisão. Inclua rua, número, bairro, cidade e CEP ou combine a entrega pelo WhatsApp.')
    return candidates[0][1]


def route_distance(address):
    global ORS_ORIGIN
    if ORS_ORIGIN is None:
        latitude = os.environ.get('RESTAURANT_LATITUDE', '')
        longitude = os.environ.get('RESTAURANT_LONGITUDE', '')
        if latitude or longitude:
            try:
                lat, lon = float(latitude), float(longitude)
                if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    raise ValueError()
            except ValueError:
                raise ValueError('A localização do restaurante precisa ser conferida. Combine a entrega pelo WhatsApp.') from None
            ORS_ORIGIN = [lon, lat]
        else:
            try:
                ORS_ORIGIN = geocode_address('Rua Nelson de Araújo, 684, Dourados, MS, Brasil')
            except ValueError:
                raise ValueError('Não conseguimos localizar o restaurante com precisão. A equipe precisa confirmar o ponto de partida antes de calcular o frete.') from None
    destination = geocode_address(address)
    result = ors_request('/v2/directions/driving-car/json', {'coordinates': [ORS_ORIGIN, destination], 'units': 'm'})
    routes = result.get('routes', [])
    meters = routes[0].get('summary', {}).get('distance') if routes else None
    if type(meters) not in (int, float) or not math.isfinite(meters) or meters < 0:
        raise ValueError('Não encontramos uma rota. Confira o endereço ou fale com o restaurante.')
    # Round upward so a fraction above a fare boundary enters the next band.
    return math.ceil(meters)


def quote_delivery(payload):
    if not isinstance(payload, dict):
        raise ValueError('Endereço inválido.')
    address = address_key(payload.get('address'))
    now = time.time()
    with db() as connection:
        row = connection.execute('SELECT * FROM delivery_quotes WHERE address=? AND expires>? ORDER BY expires DESC LIMIT 1', (address, now)).fetchone()
    if row:
        return {'quote_id': row['id'], 'distance_meters': row['meters'], 'fee': row['fee'] / 100, 'expires_at': row['expires']}
    meters = route_distance(address)
    fee = delivery_fee(meters)
    quote_id = secrets.token_urlsafe(32)
    expires = now + 900
    with db() as connection:
        connection.execute('DELETE FROM delivery_quotes WHERE expires<?', (now,))
        connection.execute('INSERT INTO delivery_quotes VALUES (?,?,?,?,?)', (quote_id, address, meters, fee, expires))
    return {'quote_id': quote_id, 'distance_meters': meters, 'fee': fee / 100, 'expires_at': expires}


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError('Pedido inválido.')
    customer = payload.get('customer')
    if not isinstance(customer, dict):
        raise ValueError('Informe seus dados.')
    name = customer.get('name', '')
    phone = customer.get('phone', '')
    notes = customer.get('notes', '')
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 100:
        raise ValueError('Informe seu nome.')
    if not isinstance(phone, str) or not 10 <= len(re.sub(r'\D', '', phone)) <= 13:
        raise ValueError('Informe um telefone válido com DDD.')
    if not isinstance(notes, str) or len(notes) > 500:
        raise ValueError('Observações inválidas.')
    fulfillment = payload.get('fulfillment')
    if fulfillment not in ('Retirada', 'Entrega'):
        raise ValueError('Escolha entrega ou retirada.')
    shipping = None
    if fulfillment == 'Entrega':
        address = address_key(payload.get('address'))
        quote_id = payload.get('quote_id')
        if not isinstance(quote_id, str):
            raise ValueError('Calcule o frete antes do pagamento.')
        with db() as connection:
            shipping = connection.execute('SELECT * FROM delivery_quotes WHERE id=? AND address=? AND expires>?', (quote_id, address, time.time())).fetchone()
        if not shipping:
            raise ValueError('Frete expirado ou endereço alterado. Calcule novamente.')
    lines = payload.get('items')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 93:
        raise ValueError('Adicione itens ao pedido.')
    items, seen, total = [], set(), 0
    for line in lines:
        if not isinstance(line, dict):
            raise ValueError('Item inválido.')
        item_id, quantity = line.get('id'), line.get('quantity')
        if not isinstance(item_id, str) or item_id not in CATALOG or item_id in seen or type(quantity) is not int or not 1 <= quantity <= 30:
            raise ValueError('Item ou quantidade inválida (máximo de 30 por item).')
        seen.add(item_id)
        item = CATALOG[item_id]
        cents = int(Decimal(str(item['price'])) * 100)
        total += cents * quantity
        items.append({'id': item_id, 'title': item['name'], 'quantity': quantity, 'currency_id': 'BRL', 'unit_price': cents / 100})
    if shipping:
        total += shipping['fee']
        items.append({'id': 'delivery-fee', 'title': 'Frete Benedetto', 'quantity': 1, 'currency_id': 'BRL', 'unit_price': shipping['fee'] / 100})
    if total > 500000:
        raise ValueError('Para pedidos acima de R$ 5.000, fale com o restaurante.')
    return {'customer': {'name': name.strip(), 'phone': re.sub(r'\D', '', phone), 'notes': notes}, 'fulfillment': fulfillment, 'address': payload.get('address', '') if shipping else '', 'items': items}, total


def verify_test_seller():
    if not TEST_MODE:
        return
    account = mp('/users/me')
    if 'test_user' not in account.get('tags', []):
        raise ValueError('Modo de teste: configure a credencial APP_USR da aplicação do vendedor de teste. Nenhum pagamento foi iniciado.')
    return account


def checkout(payload, key):
    if not ready():
        raise ValueError('Pagamento online ainda não configurado. Use o WhatsApp.')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}', key):
        raise ValueError('Identificador de pedido inválido.')
    normalized, total = validate(payload)
    verify_test_seller()
    serialized = json.dumps(normalized, sort_keys=True)
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    with LOCK:
        with db() as connection:
            row = connection.execute('SELECT * FROM orders WHERE request_key=?', (key,)).fetchone()
            if row and row['fingerprint'] != fingerprint:
                raise ValueError('O pedido mudou. Tente novamente.')
            if row and row['checkout_url'] and urlsplit(row['checkout_url']).hostname == 'www.mercadopago.com.br':
                return {'order_id': row['id'], 'checkout_url': row['checkout_url']}
            order_id = row['id'] if row else secrets.token_urlsafe(32)
            if not row:
                connection.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)', (order_id, key, fingerprint, serialized, total, 'pending', None, time.time()))
        back_url = PUBLIC_URL + '/?order=' + order_id
        preference = mp('/checkout/preferences', {
            'items': normalized['items'], 'external_reference': order_id,
            'payer': {'name': normalized['customer']['name']},
            'metadata': {'order_id': order_id, 'customer_phone': normalized['customer']['phone'], 'notes': normalized['customer']['notes'], 'fulfillment': normalized['fulfillment'], 'address': normalized['address']},
            'back_urls': {'success': back_url, 'pending': back_url, 'failure': back_url},
            'auto_return': 'approved', 'statement_descriptor': 'BENEDETTO',
        }, key)
        # Checkout Pro test accounts use the regular checkout URL.
        # Test mode is enforced by the seller-account check, not the URL.
        checkout_url = preference.get('init_point', '')
        parsed = urlsplit(checkout_url)
        if parsed.scheme != 'https' or parsed.hostname != 'www.mercadopago.com.br':
            raise RuntimeError('Unexpected checkout destination')
        with db() as connection:
            connection.execute('UPDATE orders SET checkout_url=? WHERE id=?', (checkout_url, order_id))
        return {'order_id': order_id, 'checkout_url': checkout_url}


def reconcile(order_id):
    with db() as connection:
        order = connection.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
    if not order:
        return None
    if ready() and order['checkout_url']:
        result = mp('/v1/payments/search?' + urlencode({'external_reference': order_id, 'sort': 'date_created', 'criteria': 'desc', 'limit': 100}))
        if not isinstance(result.get('results'), list):
            raise ValueError('Invalid payment search response')
        # Test sellers use the regular Checkout Pro flow with APP_USR tokens.
        # Its live_mode flag alone does not establish whether the seller is real.
        # Verify the test seller and collector instead of expecting live_mode=False.
        test_seller = verify_test_seller() if TEST_MODE else None
        if TEST_MODE and not test_seller.get('id'):
            raise ValueError('Could not verify test seller identity')
        payments = [p for p in result.get('results', []) if p.get('external_reference') == order_id
                    and (str(p.get('collector_id')) == str(test_seller['id']) if TEST_MODE else p.get('live_mode') is True)
                    and p.get('currency_id') == 'BRL'
                    and Decimal(str(p.get('transaction_amount', 0))) * 100 == order['total']]
        status = order['status']
        approved = [p for p in payments if p.get('status') == 'approved' and not p.get('transaction_amount_refunded', 0)]
        if approved:
            status = 'paid'
        elif payments:
            provider_status = payments[0].get('status')
            status = {'refunded': 'refunded', 'charged_back': 'refunded', 'rejected': 'failed', 'cancelled': 'cancelled'}.get(provider_status, 'pending')
        elif status not in ('paid', 'refunded', 'failed', 'cancelled'):
            status = 'verification_pending' if result.get('results') else 'awaiting_payment'
        with db() as connection:
            connection.execute('UPDATE orders SET status=? WHERE id=?', (status, order_id))
    else:
        status = order['status']
    result = {'order_id': order_id, 'status': status, 'total': order['total'] / 100, 'test_mode': TEST_MODE}
    if status == 'awaiting_payment' and urlsplit(order['checkout_url'] or '').hostname == 'www.mercadopago.com.br':
        result['checkout_url'] = order['checkout_url']
    return result


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, *args):
        pass  # Do not log customer data or private order links.

    def reply(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == '/api/payment-config':
            return self.reply(200, {'enabled': ready(), 'test_mode': TEST_MODE, 'provider': 'mercado_pago', 'delivery_enabled': bool(MAPS_KEY), 'online_fulfillment': ['Retirada', 'Entrega'] if MAPS_KEY else ['Retirada']})
        if parsed.path == '/api/order-status':
            order_id = parse_qs(parsed.query).get('order', [''])[0]
            if not re.fullmatch(r'[A-Za-z0-9_-]{43}', order_id):
                return self.reply(404, {'error': 'Pedido não encontrado.'})
            try:
                result = reconcile(order_id)
                return self.reply(200 if result else 404, result or {'error': 'Pedido não encontrado.'})
            except (HTTPError, URLError, TimeoutError, ValueError):
                return self.reply(503, {'error': 'Não foi possível consultar o pagamento. Tente novamente.'})
        allowed = {'/', '/index.html', '/script.js', '/style.css', '/restaurant-config.js'}
        if parsed.path not in allowed and not re.fullmatch(r'/assets/[A-Za-z0-9_.-]+\.(jpeg|jpg|png|webp)', parsed.path):
            return self.reply(404, {'error': 'Não encontrado.'})
        return super().do_GET()

    def do_HEAD(self):
        if urlsplit(self.path).path not in {'/', '/index.html', '/script.js', '/style.css', '/restaurant-config.js'}:
            return self.reply(404, {'error': 'Não encontrado.'})
        return super().do_HEAD()

    def do_POST(self):
        if self.path not in ('/api/checkout', '/api/delivery-quote'):
            return self.reply(404, {'error': 'Não encontrado.'})
        origin = self.headers.get('Origin')
        allowed_origins = {PUBLIC_URL, 'http://' + self.headers.get('Host', '')}
        if origin and origin not in allowed_origins:
            return self.reply(403, {'error': 'Origem inválida.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384:
                return self.reply(413, {'error': 'Pedido excede o limite.'})
            payload = json.loads(self.rfile.read(length))
            if self.path == '/api/delivery-quote':
                return self.reply(200, quote_delivery(payload))
            return self.reply(200, checkout(payload, self.headers.get('Idempotency-Key')))
        except (ValueError, TypeError) as error:
            return self.reply(400, {'error': str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else 'Confira os dados do pedido.'})
        except (HTTPError, URLError, TimeoutError, RuntimeError, KeyError):
            return self.reply(502, {'error': 'Não foi possível iniciar o pagamento. Tente novamente ou use o WhatsApp.'})


def sync_orders():
    while True:
        time.sleep(60)
        if not ready():
            continue
        with db() as connection:
            rows = connection.execute("SELECT id FROM orders WHERE status IN ('pending','paid','awaiting_payment','verification_pending') AND checkout_url IS NOT NULL AND created>? ORDER BY created DESC LIMIT 50", (time.time() - 7 * 86400,)).fetchall()
        for row in rows:
            try:
                reconcile(row['id'])
            except Exception:
                pass  # Keep last known status; a provider outage never marks an order paid.


if __name__ == '__main__':
    threading.Thread(target=sync_orders, daemon=True).start()
    print('Benedetto development server; online payment ' + ('configured' if ready() else 'not configured'), flush=True)
    # Hosting platforms expose PORT and require listening on all interfaces.
    host = os.environ.get('HOST') or ('0.0.0.0' if os.environ.get('PORT') or os.environ.get('RENDER') else '127.0.0.1')
    port = int(os.environ.get('PORT', '8001'))
    print(f'Listening on {host}:{port}', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
