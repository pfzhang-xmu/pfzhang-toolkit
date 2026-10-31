"""Fetch a single pinned-account entry credential; return PNG bytes only.

The PNG is a bearer credential. Callers must not log, cache, or expose it.
"""
import hashlib
import hmac
import io
import re
from urllib.parse import urlencode

import qrcode
import requests

from core.booking import orders
from core.booking.client import API, PAGES
from core.booking.session import BookingError

QR_DETAIL = '/index/stadium/orderDetails'


def find_profile(account_fingerprint):
    # Pin the read to the account the page already verified.
    from app import backend
    return backend.load_adapter().find_profile(selected=account_fingerprint)


class QRClient(orders.OrdersClient):
    """No booking/cancellation endpoints or diagnostic response recording."""
    def post(self, path, fields, referer=None, *, timeout=None):
        if path not in {'/index/Index/checkLogin', orders.LIST, orders.DETAIL, QR_DETAIL}:
            raise BookingError('ORDER_ACTION_FORBIDDEN', '只允许读取订单')
        try:
            response = self.http.post(API + path, data=fields,
                headers={'Referer': referer or PAGES + 'home.html'},
                timeout=timeout or (8, 20), allow_redirects=False)
            if response.is_redirect or response.status_code != 200:
                raise BookingError('QR_HTTP_FAILED', '读取预约失败')
            result = response.json()
        except (requests.RequestException, ValueError):
            raise BookingError('QR_HTTP_FAILED', '读取预约失败') from None
        if not isinstance(result, dict):
            raise BookingError('QR_SCHEMA_CHANGED', '预约数据结构异常')
        return result

    def require(self, path, fields, referer=None, *, timeout=None):
        result = self.post(path, fields, referer, timeout=timeout)
        if result.get('status') != 1:
            # Do not propagate arbitrary server info: it may contain credentials.
            raise BookingError('QR_READ_FAILED', '读取预约失败')
        data = result.get('data')
        if getattr(self, '_capturing_login', False) and path == orders.LIST and fields.get('p') == '1':
            self._login_first_page = data
        return data


def _fail():
    raise BookingError('QR_UNAVAILABLE', '无法确认该预约的有效入场凭证')


def _validate_detail(client, detail, oid, did):
    if not isinstance(detail, dict):
        _fail()
    orders.own(client, detail)
    if 'order_id' in detail and str(detail['order_id']) != oid:
        _fail()
    if 'audit_status' in detail and str(detail['audit_status']) != '1':
        _fail()
    matches = [item for item in orders.collection(detail.get('details')) if orders.detail_id(item) == did]
    if len(matches) != 1 or str(matches[0].get('status')) != '1':
        _fail()


def fetch_entry_qr(order_id, details_id, account_fingerprint):
    """Read fresh order status and return a local PNG, never a raw credential.

    Identifiers must be canonical strings; account_fingerprint is SHA-256(uid).
    Network operations are limited to authentication and read-only order APIs.
    """
    if (not isinstance(order_id, str) or orders.identifier(order_id) != order_id
            or not isinstance(details_id, str) or orders.identifier(details_id) != details_id
            or not isinstance(account_fingerprint, str)
            or not re.fullmatch(r'[0-9a-f]{64}', account_fingerprint)):
        _fail()
    ticket = find_profile(account_fingerprint)
    fingerprint = hashlib.sha256(ticket.uid.encode()).hexdigest()
    if not hmac.compare_digest(fingerprint, account_fingerprint):
        raise BookingError('ACCOUNT_CHANGED', '账号已改变，请重新检查')
    client = QRClient(ticket)
    try:
        if not client.login().get('account_checked'):
            raise BookingError('HTTP_AUTH_UNVERIFIED', '尚未完成订单账号核验')
        rows = orders.active_rows(client, only_order=order_id)
        if len([row for row in rows if row.get('order_id') == order_id and row.get('details_id') == details_id]) != 1:
            _fail()
        # Unlike the shared active_rows defaults, require the selected detail's
        # explicit active status on both independent order-detail responses.
        _validate_detail(client, orders.read_detail(client, order_id), order_id, details_id)
        data = client.require(QR_DETAIL, {'order_id': order_id, 'order_num': ''},
                              PAGES + 'order.html?' + urlencode({'id': order_id}), timeout=(3, 6))
        _validate_detail(client, data, order_id, details_id)
        secret = data.get('qrcode')
        if not isinstance(secret, str) or not secret.strip() or len(secret.encode('utf-8')) > 2000:
            _fail()
        try:
            image = qrcode.make(details_id + '|' + secret)
            output = io.BytesIO()
            image.save(output, format='PNG')
            return output.getvalue()
        except Exception:
            raise BookingError('QR_GENERATION_FAILED', '无法生成入场凭证') from None
    finally:
        client.http.close()
