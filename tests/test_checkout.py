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
        self.seller_guard = patch.object(server, 'verify_test_seller', return_value={'id': 123, 'tags': ['test_user']})
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

    def test_empty_search_does_not_mean_processing(self):
        with patch.object(server, 'mp', return_value={'init_point': 'https://www.mercadopago.com.br/checkout/test'}):
            order = server.checkout(self.payload, self.key)
        with patch.object(server, 'mp', return_value={'results': []}):
            result = server.reconcile(order['order_id'])
            self.assertEqual(result['status'], 'awaiting_payment')
            self.assertEqual(result['checkout_url'], order['checkout_url'])
        payment = {'external_reference': order['order_id'], 'currency_id': 'BRL', 'transaction_amount': 130, 'status': 'pending', 'live_mode': False, 'collector_id': 123}
        with patch.object(server, 'mp', return_value={'results': [payment]}):
            result = server.reconcile(order['order_id'])
            self.assertEqual(result['status'], 'pending')
            self.assertNotIn('checkout_url', result)
        with server.db() as conn:
            conn.execute("UPDATE orders SET status='paid'")
        with patch.object(server, 'mp', return_value={'results': []}):
            self.assertEqual(server.reconcile(order['order_id'])['status'], 'paid')

    def test_invalid_orders(self):
        for items in [[{'id': 'unknown', 'quantity': 1}], [{'id': 'tartine', 'quantity': -1}], [{'id': 'tartine', 'quantity': True}], [{'id': 'tartine', 'quantity': 31}], [{'id': 'tartine', 'quantity': 1}] * 2, []]:
            with self.subTest(items=items), self.assertRaises(ValueError):
                server.validate({**self.payload, 'items': items})
        with self.assertRaises(ValueError):
            server.validate({**self.payload, 'fulfillment': 'Entrega'})

    def test_payment_is_verified_not_return_parameter(self):
        with patch.object(server, 'mp', return_value={'init_point': 'https://www.mercadopago.com.br/test'}):
            order = server.checkout(self.payload, self.key)
        payment = {'external_reference': order['order_id'], 'currency_id': 'BRL', 'transaction_amount': 130, 'status': 'approved', 'live_mode': False, 'collector_id': 123}
        for wrong in [{'transaction_amount': 1}, {'currency_id': 'USD'}, {'external_reference': 'another-order'}, {'collector_id': 999}]:
            with self.subTest(wrong=wrong), patch.object(server, 'mp', return_value={'results': [{**payment, **wrong}]}):
                self.assertEqual(server.reconcile(order['order_id'])['status'], 'verification_pending')
        with patch.object(server, 'mp', return_value={'results': [payment]}):
            self.assertEqual(server.reconcile(order['order_id'])['status'], 'paid')
        with patch.object(server, 'mp', return_value={'results': [{**payment, 'status': 'refunded', 'transaction_amount_refunded': 130}]}):
            self.assertEqual(server.reconcile(order['order_id'])['status'], 'refunded')

    def test_test_seller_standard_checkout_can_report_live_mode(self):
        with patch.object(server, 'mp', return_value={'init_point': 'https://www.mercadopago.com.br/test'}):
            order = server.checkout(self.payload, self.key)
        payment = {'external_reference': order['order_id'], 'currency_id': 'BRL', 'transaction_amount': 130, 'status': 'approved', 'live_mode': True, 'collector_id': 123}
        with patch.object(server, 'mp', return_value={'results': [payment]}):
            self.assertEqual(server.reconcile(order['order_id'])['status'], 'paid')
        with patch.object(server, 'verify_test_seller', side_effect=ValueError('Real seller')), patch.object(server, 'mp', return_value={'results': [payment]}):
            with self.assertRaises(ValueError):
                server.reconcile(order['order_id'])

    def test_production_rejects_sandbox_payment(self):
        with patch.object(server, 'mp', return_value={'init_point': 'https://www.mercadopago.com.br/test'}):
            order = server.checkout(self.payload, self.key)
        payment = {'external_reference': order['order_id'], 'currency_id': 'BRL', 'transaction_amount': 130, 'status': 'approved', 'live_mode': False, 'collector_id': 123}
        with patch.object(server, 'TEST_MODE', False), patch.object(server, 'mp', return_value={'results': [payment]}):
            self.assertEqual(server.reconcile(order['order_id'])['status'], 'verification_pending')

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

    def test_map_point_is_confirmed_and_bound_to_quote(self):
        payload = {'address': 'Rua Teste, 100, Centro, Dourados MS', 'destination': {'latitude': -22.23, 'longitude': -54.81}, 'pin_confirmed': True}
        with patch.object(server, 'route_distance', return_value=4000) as route:
            quote = server.quote_delivery(payload)
            route.assert_called_once_with(server.address_key(payload['address']), [-54.81, -22.23])
        order = {**self.payload, **payload, 'fulfillment': 'Entrega', 'quote_id': quote['quote_id']}
        normalized, total = server.validate(order)
        self.assertEqual(total, 13750)
        self.assertEqual(normalized['destination'], payload['destination'])
        with self.assertRaises(ValueError):
            server.validate({**order, 'destination': {'latitude': -22.24, 'longitude': -54.81}})
        with self.assertRaises(ValueError):
            server.quote_delivery({**payload, 'pin_confirmed': False})
        with self.assertRaises(ValueError):
            server.quote_delivery({**payload, 'destination': {'latitude': float('nan'), 'longitude': -54.81}})

    def test_changed_order_cannot_reuse_key(self):
        with patch.object(server, 'mp', return_value={'init_point': 'https://www.mercadopago.com.br/test'}):
            server.checkout(self.payload, self.key)
            with self.assertRaises(ValueError):
                server.checkout({**self.payload, 'items': [{'id': 'tartine', 'quantity': 1}]}, self.key)


class OpenRouteServiceTests(unittest.TestCase):
    def setUp(self):
        settings = patch.multiple(server, MAPS_KEY='local-ors-placeholder', ORS_ORIGIN=None)
        settings.start()
        self.addCleanup(settings.stop)
        location = patch.dict(server.CONFIG, {'restaurant_location': {}})
        location.start()
        self.addCleanup(location.stop)

    def test_confirmed_restaurant_pin_is_used_without_origin_search(self):
        location = {'latitude': -22.22596060467644, 'longitude': -54.80929650358167}
        with patch.dict(server.CONFIG, {'restaurant_location': location}), patch.dict(os.environ, {'RESTAURANT_LATITUDE': '', 'RESTAURANT_LONGITUDE': ''}), patch.object(server, 'geocode_address', return_value=[-54.81, -22.23]) as geocode, patch.object(server, 'ors_request', return_value={'routes': [{'summary': {'distance': 1000}}]}) as api:
            self.assertEqual(server.route_distance('Rua Teste, 100, Dourados MS'), 1000)
            geocode.assert_called_once_with('Rua Teste, 100, Dourados MS')
            self.assertEqual(api.call_args.args[1]['coordinates'][0], [location['longitude'], location['latitude']])

    def feature(self, coordinates, number='684', confidence=1):
        return {'geometry': {'coordinates': coordinates}, 'properties': {'layer': 'address', 'housenumber': number, 'confidence': confidence}}

    def test_geocoding_route_and_origin_cache(self):
        origin = self.feature([-54.80, -22.22])
        destination = self.feature([-54.81, -22.23], '100')
        with patch.object(server, 'ors_request', side_effect=[{'features': [origin]}, {'features': [destination]}, {'routes': [{'summary': {'distance': 3000.1}}]}, {'features': [destination]}, {'routes': [{'summary': {'distance': 3000}}]}]) as api:
            self.assertEqual(server.route_distance('Rua Teste, 100, Dourados MS'), 3001)
            self.assertEqual(server.route_distance('Rua Teste, 100, Dourados MS'), 3000)
            route_calls = [call for call in api.call_args_list if '/directions/' in call.args[0]]
            self.assertEqual(route_calls[0].args[1]['coordinates'], [[-54.80, -22.22], [-54.81, -22.23]])
            self.assertEqual(api.call_count, 5)

    def test_explicit_origin_coordinates_bypass_origin_search(self):
        with patch.dict(os.environ, {'RESTAURANT_LATITUDE': '-22.22', 'RESTAURANT_LONGITUDE': '-54.80'}), patch.object(server, 'geocode_address', return_value=[-54.81,-22.23]) as geocode, patch.object(server, 'ors_request', return_value={'routes': [{'summary': {'distance': 5000}}]}) as route:
            self.assertEqual(server.route_distance('Rua Teste, 100, Dourados MS'), 5000)
            geocode.assert_called_once_with('Rua Teste, 100, Dourados MS')
            self.assertEqual(route.call_args.args[1]['coordinates'], [[-54.80,-22.22],[-54.81,-22.23]])

    def test_invalid_origin_coordinates_do_not_create_a_route(self):
        with patch.dict(os.environ, {'RESTAURANT_LATITUDE': 'nan', 'RESTAURANT_LONGITUDE': '-54.80'}), patch.object(server, 'ors_request') as route:
            with self.assertRaisesRegex(ValueError, 'localização do restaurante'):
                server.route_distance('Rua Teste, 100, Dourados MS')
            route.assert_not_called()

    def test_inaccurate_and_ambiguous_addresses_are_rejected(self):
        for features in [[], [self.feature([-54.8,-22.2], confidence=0.5)], [self.feature([-54.8,-22.2], number='999')], [self.feature([-54.8,-22.2]), self.feature([-54.9,-22.3])]]:
            with self.subTest(features=features), patch.object(server, 'ors_request', return_value={'features': features}), self.assertRaises(ValueError):
                server.geocode_address('Rua Nelson de Araújo, 684, Dourados MS')

    def test_quota_error_and_server_only_authentication(self):
        from urllib.error import HTTPError
        with patch.object(server, 'urlopen', side_effect=HTTPError('https://api.openrouteservice.org',429,'Too many requests',{},None)) as http:
            with self.assertRaisesRegex(ValueError, 'limite'):
                server.ors_request('/geocode/search?text=teste')
            request = http.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), 'local-ors-placeholder')
            self.assertNotIn('local-ors-placeholder', request.full_url)
        with patch.object(server, 'MAPS_KEY', ''), self.assertRaisesRegex(ValueError, 'não ativado'):
            server.ors_request('/geocode/search?text=teste')


if __name__ == '__main__':
    unittest.main()
