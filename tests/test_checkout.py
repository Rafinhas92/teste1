import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory()
os.environ['ORDER_DATA_DIR'] = TEMP.name
spec = importlib.util.spec_from_file_location('checkout_server', 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        with server.db() as conn:
            conn.execute('DELETE FROM orders')
            conn.execute('DELETE FROM delivery_quotes')
        self.settings = patch.multiple(server, TOKEN='local-test-placeholder', PUBLIC_URL='https://example.com', TEST_MODE=True)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.seller_guard = patch.object(server, 'verify_test_seller')
        self.seller_guard.start()
        self.addCleanup(self.seller_guard.stop)
        self.payload = {'customer': {'name': 'Teste Local', 'phone': '67999999999', 'notes': 'Sem cebola'}, 'fulfillment': 'Retirada', 'items': [{'id': 'tartine', 'quantity': 2, 'price': 0.01}], 'total': 0.01}
        self.key = '00000000-0000-4000-8000-000000000000'

    def test_server_prices_and_idempotency(self):
        with patch.object(server, 'mp', return_value={'init_point': 'https://www.mercadopago.com.br/checkout/test'}) as gateway:
            first = server.checkout(self.payload, self.key)
            second = server.checkout(self.payload, self.key)
            self.assertEqual(first, second)
            gateway.assert_called_once()
            preference = gateway.call_args.args[1]
            self.assertEqual(preference['items'][0]['unit_price'], 65)
            self.assertEqual(preference['metadata']['notes'], 'Sem cebola')
        with server.db() as conn:
            row = conn.execute('SELECT * FROM orders').fetchone()
            self.assertEqual(row['total'], 13000)
            self.assertEqual(row['status'], 'pending')

    def test_uses_regular_checkout_and_refreshes_old_sandbox_link(self):
        result = {'init_point': 'https://www.mercadopago.com.br/checkout/test', 'sandbox_init_point': 'https://sandbox.mercadopago.com.br/old'}
        with patch.object(server, 'mp', return_value=result):
            first = server.checkout(self.payload, self.key)
            self.assertEqual(first['checkout_url'], result['init_point'])
            with server.db() as conn:
                conn.execute('UPDATE orders SET checkout_url=?', (result['sandbox_init_point'],))
            updated = server.checkout(self.payload, self.key)
            self.assertEqual(updated['order_id'], first['order_id'])
            self.assertEqual(updated['checkout_url'], result['init_point'])

    def test_real_seller_is_blocked_in_test_mode(self):
        self.seller_guard.stop()
        with patch.object(server, 'mp', return_value={'tags': []}) as gateway:
            with self.assertRaisesRegex(ValueError, 'vendedor de teste'):
                server.checkout(self.payload, self.key)
            gateway.assert_called_once_with('/users/me')
        with server.db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM orders').fetchone()[0], 0)
        with patch.object(server, 'mp', return_value={'tags': ['test_user']}):
            server.verify_test_seller()

    def test_invalid_orders(self):
        for items in [[{'id': 'unknown', 'quantity': 1}], [{'id': 'tartine', 'quantity': -1}], [{'id': 'tartine', 'quantity': True}], [{'id': 'tartine', 'quantity': 31}], [{'id': 'tartine', 'quantity': 1}] * 2, []]:
            with self.subTest(items=items), self.assertRaises(ValueError):
                server.validate({**self.payload, 'items': items})
        with self.assertRaises(ValueError):
            server.validate({**self.payload, 'fulfillment': 'Entrega'})

    def test_payment_is_verified_not_return_parameter(self):
        with patch.object(server, 'mp', return_value={'init_point': 'https://www.mercadopago.com.br/test'}):
            order = server.checkout(self.payload, self.key)
        payment = {'external_reference': order['order_id'], 'currency_id': 'BRL', 'transaction_amount': 130, 'status': 'approved', 'live_mode': False}
        for wrong in [{'transaction_amount': 1}, {'currency_id': 'USD'}, {'external_reference': 'another-order'}, {'live_mode': True}]:
            with self.subTest(wrong=wrong), patch.object(server, 'mp', return_value={'results': [{**payment, **wrong}]}):
                self.assertEqual(server.reconcile(order['order_id'])['status'], 'pending')
        with patch.object(server, 'mp', return_value={'results': [payment]}):
            self.assertEqual(server.reconcile(order['order_id'])['status'], 'paid')
        with patch.object(server, 'mp', return_value={'results': [{**payment, 'status': 'refunded', 'transaction_amount_refunded': 130}]}):
            self.assertEqual(server.reconcile(order['order_id'])['status'], 'refunded')

    def test_missing_configuration_and_wrong_redirect(self):
        with patch.object(server, 'TOKEN', ''), self.assertRaises(ValueError):
            server.checkout(self.payload, self.key)
        with patch.object(server, 'mp', return_value={'init_point': 'https://malicious.example/test'}), self.assertRaises(RuntimeError):
            server.checkout(self.payload, self.key)

    def test_delivery_price_boundaries(self):
        for meters, cents in [(0,650),(3000,650),(3001,750),(5000,750),(5001,850),(7000,850),(7001,1000),(15000,1000)]:
            with self.subTest(meters=meters):
                self.assertEqual(server.delivery_fee(meters), cents)

    def test_shipping_quote_is_verified_and_expires(self):
        address = 'Rua de Teste, 100, Centro, Dourados MS, 79800-000'
        with patch.object(server, 'route_distance', return_value=5001) as route:
            quote = server.quote_delivery({'address': address})
            cached = server.quote_delivery({'address': address})
            self.assertEqual(quote, cached)
            route.assert_called_once()
        payload = {**self.payload, 'fulfillment': 'Entrega', 'address': address, 'quote_id': quote['quote_id'], 'shipping_fee': 0.01}
        normalized, total = server.validate(payload)
        self.assertEqual(total, 13850)
        self.assertEqual(normalized['items'][-1]['unit_price'], 8.5)
        with self.assertRaises(ValueError):
            server.validate({**payload, 'address': 'Outro endereço, 123, Dourados MS'})
        with self.assertRaises(ValueError):
            server.validate({**payload, 'quote_id': 'forged'})
        with server.db() as conn:
            conn.execute('UPDATE delivery_quotes SET expires=0')
        with self.assertRaises(ValueError):
            server.validate(payload)

    def test_changed_order_cannot_reuse_key(self):
        with patch.object(server, 'mp', return_value={'init_point': 'https://www.mercadopago.com.br/test'}):
            server.checkout(self.payload, self.key)
            with self.assertRaises(ValueError):
                server.checkout({**self.payload, 'items': [{'id': 'tartine', 'quantity': 1}]}, self.key)


if __name__ == '__main__':
    unittest.main()
