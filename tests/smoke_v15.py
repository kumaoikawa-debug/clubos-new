from pathlib import Path
import os, sys, json, base64, time, secrets

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v15_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import app
from fastapi.testclient import TestClient
from payment_providers import WeChatPayV3Provider, AlipayProvider, merchant_order_no, provider_refund_no
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.15','0.16','0.17','0.18','0.19','0.20','0.21','0.22','0.23','0.24','0.25'))
club_acc=ok('get','/api/club/1/payment-account').json(); platform_acc=ok('get','/api/platform/payment-account').json()
assert club_acc['scope_type']=='club' and club_acc['provider']=='local'
assert platform_acc['scope_type']=='platform' and platform_acc['provider']=='local'
assert 'private_key' not in json.dumps(club_acc).lower()

# Activity checkout -> club payment account.
aid=ok('get','/api/public/clubs/1/activities').json()[0]['id']
a=ok('get',f'/api/public/activities/{aid}').json(); oid=a['occurrences'][0]['id']
co=ok('post',f'/api/public/activities/{aid}/checkout',json={'name':'林野','phone':'13800000001','occurrenceId':oid,'clubPoints':0,'gearPoints':0,'participants':[{'name':'林野','phone':'13800000001'}]}).json()
pay=ok('post',f"/api/public/checkouts/{co['checkoutId']}/pay",json={'simulateSuccess':True}).json()
assert pay['paymentStatus']=='succeeded'
with app.conn() as dbc:
    pa=dict(dbc.execute('SELECT * FROM payment_attempts WHERE checkout_intent_id=? ORDER BY created_at DESC LIMIT 1',(co['checkoutId'],)).fetchone())
assert pa['payment_account_id']==club_acc['id'] and len(pa['merchant_order_no'])<=32

# Gear checkout -> platform payment account.
g=ok('post','/api/public/clubs/1/gear-checkout',json={'userId':1,'items':[{'productId':1,'quantity':1}],'gearPoints':0}).json()
gp=ok('post',f"/api/public/checkouts/{g['checkoutId']}/pay",json={'simulateSuccess':True}).json()
assert gp['paymentStatus']=='succeeded'
with app.conn() as dbc:
    ga=dict(dbc.execute('SELECT * FROM payment_attempts WHERE checkout_intent_id=? ORDER BY created_at DESC LIMIT 1',(g['checkoutId'],)).fetchone())
assert ga['payment_account_id']==platform_acc['id'] and len(ga['merchant_order_no'])<=32
assert len(merchant_order_no())<=32 and len(provider_refund_no())<=32

# Payment account API never stores secrets; it stores credential_ref only.
ua=ok('patch','/api/club/1/payment-account',json={'provider':'wechatpay_v3','channel':'native','merchantId':'1900000001','appId':'wx_demo','credentialRef':'club_1_test_wechat','enabled':True}).json()
assert ua['provider']=='wechatpay_v3' and ua['credential_ref']=='club_1_test_wechat'
# Restore local so subsequent app regression is deterministic.
ok('patch','/api/club/1/payment-account',json={'provider':'local','channel':'mock','credentialRef':'club_1_local','enabled':True})

# WeChat callback signature + AES-GCM decryption can be verified offline with generated keys.
merchant_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
platform_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
def priv_pem(k):return k.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
def pub_pem(k):return k.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
os.environ['PAYMENT_CRED_WXTEST_PRIVATE_KEY']=priv_pem(merchant_key)
os.environ['PAYMENT_CRED_WXTEST_SERIAL_NO']='SERIAL123'
os.environ['PAYMENT_CRED_WXTEST_API_V3_KEY']='12345678901234567890123456789012'
os.environ['PAYMENT_CRED_WXTEST_PLATFORM_PUBLIC_KEY']=pub_pem(platform_key)
wxacct={'provider':'wechatpay_v3','channel':'native','merchant_id':'1900000001','app_id':'wx_demo','credential_ref':'wxtest'}
wx=WeChatPayV3Provider(wxacct)
resource={'out_trade_no':'C123','transaction_id':'WXTX1','trade_state':'SUCCESS','amount':{'total':1234}}
nonce='abcdefghijkl'; aad='transaction'; aes=AESGCM(os.environ['PAYMENT_CRED_WXTEST_API_V3_KEY'].encode())
cipher=aes.encrypt(nonce.encode(),json.dumps(resource,separators=(',',':')).encode(),aad.encode())
envelope={'id':'evt_wx_1','resource':{'nonce':nonce,'associated_data':aad,'ciphertext':base64.b64encode(cipher).decode()}}
raw=json.dumps(envelope,separators=(',',':')).encode(); ts=str(int(time.time())); cbnonce=secrets.token_hex(8)
msg=f"{ts}\n{cbnonce}\n{raw.decode()}\n"; sig=platform_key.sign(msg.encode(),padding.PKCS1v15(),hashes.SHA256())
verified=wx.verify_callback({'Wechatpay-Timestamp':ts,'Wechatpay-Nonce':cbnonce,'Wechatpay-Signature':base64.b64encode(sig).decode()},raw)
assert verified['resource']['transaction_id']=='WXTX1'

# Alipay callback RSA2 verification offline.
ali_app=rsa.generate_private_key(public_exponent=65537,key_size=2048)
ali_platform=rsa.generate_private_key(public_exponent=65537,key_size=2048)
os.environ['PAYMENT_CRED_ALITEST_PRIVATE_KEY']=priv_pem(ali_app)
os.environ['PAYMENT_CRED_ALITEST_ALIPAY_PUBLIC_KEY']=pub_pem(ali_platform)
aliacct={'provider':'alipay','channel':'wap','app_id':'2026000000000000','credential_ref':'alitest'}
ali=AlipayProvider(aliacct)
form={'app_id':'2026000000000000','out_trade_no':'CALI1','trade_no':'ALI_TX_1','trade_status':'TRADE_SUCCESS','total_amount':'88.00','notify_id':'notify_1'}
content='&'.join(f"{k}={form[k]}" for k in sorted(form)); sign=ali_platform.sign(content.encode(),padding.PKCS1v15(),hashes.SHA256())
form['sign']=base64.b64encode(sign).decode(); form['sign_type']='RSA2'
assert ali.verify_callback(form)['trade_no']=='ALI_TX_1'

print('SMOKE V0.15 OK · PAYMENT ACCOUNT ROUTING / PROVIDER ORDER NOS / LOCAL PAYMENT / WECHAT CALLBACK CRYPTO / ALIPAY CALLBACK RSA2')
