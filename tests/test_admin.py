import http.client
import json
import secrets
import threading
import time
import unittest
from unittest.mock import patch
from test_checkout import server


class AdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verifier = server.password_hash('fixture-only-password-123')

    def setUp(self):
        self.settings = patch.multiple(server, ADMIN_VERIFIER=self.verifier, PUBLIC_URL='')
        self.settings.start()
        self.addCleanup(self.settings.stop)
        server.LOGIN_FAILURES.clear()
        with server.db() as conn:
            conn.execute('DELETE FROM admin_sessions')
            conn.execute('DELETE FROM order_events')
            conn.execute('DELETE FROM orders')
        self.order = secrets.token_urlsafe(32)
        payload = {'customer': {'name': '<img src=x onerror=alert(1)>', 'phone': '00000000000', 'notes': 'Sem cebola'}, 'fulfillment': 'Retirada', 'address': '', 'items': [{'id': 'tartine', 'title': 'Tartine', 'quantity': 1, 'unit_price': 65}], 'destination': None}
        with server.db() as conn:
            conn.execute('INSERT INTO orders(id,request_key,fingerprint,payload,total,status,created,payment_test_mode) VALUES(?,?,?,?,?,?,?,?)', (self.order,'fixture-key','fingerprint',json.dumps(payload),6500,'paid',time.time(),int(server.TEST_MODE)))
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=self.httpd.serve_forever,daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.origin = 'http://127.0.0.1:' + str(self.httpd.server_port)

    def request(self,path,body=None,cookie=None,csrf=None,origin=True):
        headers={}
        if cookie: headers['Cookie']=cookie
        if csrf: headers['X-CSRF-Token']=csrf
        if body is not None:
            headers['Content-Type']='application/json'
            if origin: headers['Origin']=self.origin
        client=http.client.HTTPConnection('127.0.0.1',self.httpd.server_port,timeout=5)
        client.request('GET' if body is None else 'POST',path,body=None if body is None else json.dumps(body),headers=headers)
        response=client.getresponse(); raw=response.read();status=response.status;headers=dict(response.getheaders());client.close()
        return status,headers,json.loads(raw) if headers.get('Content-Type','').startswith('application/json') else raw

    def login(self):
        code,headers,result=self.request('/api/admin/login',{'password':'fixture-only-password-123'})
        self.assertEqual(code,200)
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        return headers['Set-Cookie'].split(';')[0],result['csrf']

    def test_private_orders_and_login(self):
        code,_,body=self.request('/api/admin/orders');self.assertEqual(code,401);self.assertNotIn('orders',body)
        cookie,csrf=self.login()
        code,headers,result=self.request('/api/admin/orders',cookie=cookie)
        self.assertEqual(code,200);self.assertEqual(result['orders'][0]['id'],self.order)
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertNotIn('checkout_url',result['orders'][0])
        self.assertNotIn('csrf',result)
        code,_,state=self.request('/api/admin/session',cookie=cookie);self.assertEqual(state['csrf'],csrf)

    def test_csrf_and_cross_origin(self):
        cookie,csrf=self.login();path='/api/admin/orders/'+self.order+'/stage'
        payload={'stage':'preparing','expected':'received'}
        self.assertEqual(self.request(path,payload,cookie=cookie)[0],403)
        self.assertEqual(self.request(path,payload,cookie=cookie,csrf='invalid')[0],403)
        self.assertEqual(self.request(path,payload,cookie=cookie,csrf=csrf,origin=False)[0],403)
        with server.db() as conn:self.assertEqual(conn.execute('SELECT fulfillment_status FROM orders').fetchone()[0],'received')

    def test_payment_guard_progress_and_audit(self):
        cookie,csrf=self.login();path='/api/admin/orders/'+self.order+'/stage'
        with server.db() as conn:conn.execute("UPDATE orders SET status='pending'")
        self.assertEqual(self.request(path,{'stage':'preparing','expected':'received'},cookie,csrf)[0],400)
        with server.db() as conn:conn.execute("UPDATE orders SET status='paid'")
        self.assertEqual(self.request(path,{'stage':'preparing','expected':'received'},cookie,csrf)[0],200)
        self.assertEqual(self.request(path,{'stage':'ready','expected':'received'},cookie,csrf)[0],409)
        self.assertEqual(self.request(path,{'stage':'out_for_delivery','expected':'preparing'},cookie,csrf)[0],400)
        self.assertEqual(self.request(path,{'stage':'ready','expected':'preparing'},cookie,csrf)[0],200)
        self.assertEqual(self.request(path,{'stage':'completed','expected':'ready'},cookie,csrf)[0],200)
        self.assertEqual(self.request(path,{'stage':'cancelled','expected':'completed'},cookie,csrf)[0],400)
        with server.db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM order_events').fetchone()[0],3)
            self.assertEqual(conn.execute('SELECT status FROM orders').fetchone()[0],'paid')

    def test_delivery_status_flow(self):
        with server.db() as conn:
            payload=json.loads(conn.execute('SELECT payload FROM orders').fetchone()[0])
            payload['fulfillment']='Entrega'
            conn.execute('UPDATE orders SET payload=?',(json.dumps(payload),))
        cookie,csrf=self.login();path='/api/admin/orders/'+self.order+'/stage'
        for current,next_stage in [('received','preparing'),('preparing','out_for_delivery'),('out_for_delivery','completed')]:
            self.assertEqual(self.request(path,{'stage':next_stage,'expected':current},cookie,csrf)[0],200)

    def test_test_orders_cannot_advance_in_production(self):
        cookie,csrf=self.login()
        with patch.object(server,'TEST_MODE',False):
            self.assertEqual(self.request('/api/admin/orders/'+self.order+'/stage',{'stage':'preparing','expected':'received'},cookie,csrf)[0],400)

    def test_cancel_does_not_refund_or_change_payment(self):
        cookie,csrf=self.login()
        self.assertEqual(self.request('/api/admin/orders/'+self.order+'/stage',{'stage':'cancelled','expected':'received'},cookie,csrf)[0],200)
        with server.db() as conn:
            row=conn.execute('SELECT status,fulfillment_status FROM orders').fetchone()
            self.assertEqual(tuple(row),('paid','cancelled'))

    def test_expiry_password_rotation_and_logout(self):
        cookie,csrf=self.login()
        code,_,_=self.request('/api/admin/logout',{},cookie,csrf);self.assertEqual(code,200)
        self.assertEqual(self.request('/api/admin/orders',cookie=cookie)[0],401)
        cookie,csrf=self.login()
        with server.db() as conn:conn.execute('UPDATE admin_sessions SET expires=0')
        self.assertEqual(self.request('/api/admin/orders',cookie=cookie)[0],401)
        cookie,csrf=self.login()
        with patch.object(server,'ADMIN_VERIFIER',server.password_hash('another-fixture-password')):
            self.assertEqual(self.request('/api/admin/orders',cookie=cookie)[0],401)

    def test_login_rate_limit_and_missing_password(self):
        for _ in range(5):self.assertEqual(self.request('/api/admin/login',{'password':'wrong'})[0],401)
        self.assertEqual(self.request('/api/admin/login',{'password':'fixture-only-password-123'})[0],429)
        with patch.object(server,'ADMIN_VERIFIER',None):
            self.assertEqual(self.request('/api/admin/login',{'password':'fixture-only-password-123'})[0],503)

    def test_admin_page_and_private_files(self):
        code,headers,body=self.request('/admin')
        self.assertEqual(code,200);self.assertIn(b'login-form',body)
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        for path in ['/server.py','/.local/orders.sqlite3','/.env','/tests/test_admin.py']:
            self.assertEqual(self.request(path)[0],404)

    def test_filter_and_refresh_access(self):
        cookie,csrf=self.login()
        code,_,body=self.request('/api/admin/orders?stage=completed',cookie=cookie)
        self.assertEqual(body['orders'],[])
        self.assertEqual(self.request('/api/admin/orders?stage=invalid',cookie=cookie)[0],400)
        self.assertEqual(self.request('/api/admin/orders?page=0',cookie=cookie)[0],400)
        with patch.object(server,'reconcile',return_value={'status':'paid'}) as reconcile:
            self.assertEqual(self.request('/api/admin/orders/'+self.order+'/refresh',{},cookie,csrf)[0],200)
            reconcile.assert_called_once_with(self.order)


if __name__=='__main__':unittest.main()
