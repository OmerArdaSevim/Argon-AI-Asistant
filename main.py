import speech_recognition as sr
import edge_tts
import asyncio
import os
import datetime
import requests
import pickle
import threading
import queue
import ctypes
import warnings
import json
import io
import re
import time
import signal
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from groq import Groq
import vlc
import yt_dlp
from ytmusicapi import YTMusic

# --- C SEVİYESİNDE ALSA LOGLARINI SUSTURMA ---
try:
    ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
    def py_error_handler(filename, line, function, err, fmt): pass
    c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
    asound = ctypes.cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except: pass

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
warnings.filterwarnings("ignore")
import pygame

# Google Takvim Kütüphaneleri
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# --- YÜKLEMELER VE AYARLAR ---
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar']

groq_istemci = Groq(api_key=GROQ_API_KEY)
pygame.mixer.init()
metin_kuyrugu = queue.Queue()
ses_kuyrugu = queue.Queue()

yt_music_istemci = YTMusic()
vlc_ornek = vlc.Instance('--no-video', '--network-caching=5000')
vlc_oynatici = vlc_ornek.media_player_new()

asistan_konusuyor = False
muzik_ses_seviyesi = 100
mesaj_gecmisi = []
MAKSIMUM_MESAJ_SAYISI = 6

# --- YOUTUBE SARI UYARILARI SUSTURUCU ---
class SessizLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

# --- AUDIO DUCKING (SES KISMA - GERİ GETİRİLDİ!) ---
def muzik_sesini_ayarla(seviye):
    global muzik_ses_seviyesi
    vlc_oynatici.audio_set_volume(seviye)
    if seviye > 30:
        muzik_ses_seviyesi = seviye

def bellegi_temizle():
    global mesaj_gecmisi
    mesaj_gecmisi.clear()
    tz_istanbul = ZoneInfo("Europe/Istanbul")
    su_an = datetime.datetime.now(tz_istanbul).strftime("%d %B %Y %A, Saat %H:%M")

    sistem_mesaji = f"""
    Sen profesyonel, zeki ve yetenekli bir yapay zeka asistanısın. Adın Argon. Şu anki zaman: {su_an}.
    - İLETİŞİM TARZI: Sadece "evet/hayır" veya "açtım/kapattım" gibi aşırı robotik cevaplar verme. Yanıtların doğal, akıcı ve insansı olsun ancak gereksiz yere lafı da uzatma. Öz ve tatmin edici konuş.
    - Araçlar (tools) bir işlem yaptığında veya hata verdiğinde, durumu doğal bir asistan gibi kullanıcıya bildir.
    - Takvim etkinlikleri için saat belirtilmemişse varsayılan olarak saat 09:00:00'ı kullan.

    [ÖZEL PROTOKOL: DNA ANALİZİ VE ÖNERİ SİSTEMİ]
    Eğer kullanıcı bir film, dizi, oyun veya müzik verip "öneri" isterse şu adımları izle:
    1. Verilen eserin DNA HARİTASINI ÇIKAR: Atmosfer, tempo, ana temalar, alt metinler, mekanikler veya sinematografik ritim.
    2. Popüler ve klişe önerilerden kesinlikle UZAK DUR. Gerçekten yapısal (DNA) olarak benzeyen, ince eleyip sık dokunmuş EN FAZLA 3 eser öner.
    3. Önerdiğin her eserin, orijinal eserin DNA'sıyla "hangi noktalarda kusursuz eşleştiğini" açıkla.
    """
    mesaj_gecmisi.append({"role": "system", "content": sistem_mesaji})

def hafiza_kontrolu():
    global mesaj_gecmisi
    while len(mesaj_gecmisi) > MAKSIMUM_MESAJ_SAYISI + 1:
        del mesaj_gecmisi[1:3]

# --- GOOGLE TAKVİM YETKİLENDİRME ---
def get_calendar_service():
    creds = None
    if os.path.exists('token.json'):
        with open('token.json', 'rb') as token: creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                return None
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', CALENDAR_SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'wb') as token: pickle.dump(creds, token)
    return build('calendar', 'v3', credentials=creds)

# --- ARAÇLAR ---
def takvime_etkinlik_ekle(ozet, baslangic_zamani, bitis_zamani):
    print(f"\n[Araç] Takvime Ekleniyor:\n - Özet: {ozet}\n - Başlangıç: {baslangic_zamani}\n - Bitiş: {bitis_zamani}", flush=True)
    try:
        service = get_calendar_service()
        if not service: return "credentials.json dosyası bulunamadı."

        etkinlik = {
            'summary': ozet,
            'start': {'dateTime': baslangic_zamani, 'timeZone': 'Europe/Istanbul'},
            'end': {'dateTime': bitis_zamani, 'timeZone': 'Europe/Istanbul'},
        }
        service.events().insert(calendarId='primary', body=etkinlik).execute()
        return f"'{ozet}' etkinliği takvime başarıyla eklendi."
    except Exception as e:
        print(f"[Araç Hatası] Google Takvim API reddetti: {str(e)}", flush=True)
        return "Takvim API format hatası nedeniyle etkinlik eklenemedi."

def takvim_listele():
    print(f"\n[Araç] Takvim listesi çekiliyor...", flush=True)
    try:
        service = get_calendar_service()
        if not service: return "Takvim anahtarı bulunamadı."
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(calendarId='primary', timeMin=now, maxResults=5, singleEvents=True, orderBy='startTime').execute()
        events = events_result.get('items', [])
        if not events: return "Yakın zamanda bir etkinliğin yok."
        res = "Etkinliklerin: "
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            res += f"{event['summary']} ({start}), "
        return res
    except Exception: return "Takvime erişilemedi."

def hava_durumu_sorgula(sehir):
    print(f"\n[Araç] Hava durumu sorgulanıyor: {sehir}", flush=True)
    try:
        url = "http://api.weatherapi.com/v1/forecast.json"
        cevap = requests.get(url, params={"key": WEATHER_API_KEY, "q": sehir, "days": 1, "lang": "tr"})
        veri = cevap.json()
        if cevap.status_code != 200: return f"'{sehir}' için hava durumu bulunamadı."
        saatlik = veri['forecast']['forecastday'][0]['hour']
        aktif = [int(s['temp_c']) for s in saatlik if 8 <= int(s['time'].split(' ')[1].split(':')[0]) <= 23]
        min_s, max_s = (min(aktif), max(aktif)) if aktif else (round(veri['forecast']['forecastday'][0]['day']['mintemp_c']), round(veri['forecast']['forecastday'][0]['day']['maxtemp_c']))
        return f"{sehir} bugün {min_s} ile {max_s} derece arasında, durum: {veri['forecast']['forecastday'][0]['day']['condition']['text']}."
    except Exception: return "Hava durumu servisi yanıt vermiyor."

def muzik_cal(sarki_adi):
    print(f"\n[Araç] Şarkı aranıyor: {sarki_adi}", flush=True)
    try:
        aramalar = yt_music_istemci.search(sarki_adi, filter="songs", limit=1)
        if not aramalar: return f"{sarki_adi} bulunamadı."
        video_id, baslik = aramalar[0]['videoId'], aramalar[0]['title']
        url = f"https://music.youtube.com/watch?v={video_id}"

        ydl_ayarlar = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'quiet': True, 'no_warnings': True, 'noplaylist': True,
            'logger': SessizLogger(),
            'extractor_args': {'youtube': ['player_client=android,ios', 'player_skip=webpage,js']}
        }

        with yt_dlp.YoutubeDL(ydl_ayarlar) as ydl:
            yayin_url = ydl.extract_info(url, download=False)['url']
        vlc_oynatici.set_media(vlc_ornek.media_new(yayin_url))
        vlc_oynatici.play()
        return f"{baslik} çalınıyor."
    except Exception: return "Müzik başlatılamadı."

def muzik_kontrol(komut, seviye=None):
    print(f"\n[Araç] Müzik komutu: {komut}", flush=True)
    global muzik_ses_seviyesi
    try:
        if komut == "durdur": vlc_oynatici.pause(); return "Müzik durduruldu."
        elif komut == "devam": vlc_oynatici.play(); return "Müzik devam ediyor."
        elif komut == "kapat": vlc_oynatici.stop(); return "Müzik kapatıldı."
        elif komut == "sesi_arttir":
            muzik_ses_seviyesi = min(100, muzik_ses_seviyesi + 20)
            vlc_oynatici.audio_set_volume(muzik_ses_seviyesi)
            return "Ses artırıldı."
        elif komut == "sesi_azalt":
            muzik_ses_seviyesi = max(0, muzik_ses_seviyesi - 20)
            vlc_oynatici.audio_set_volume(muzik_ses_seviyesi)
            return "Ses azaltıldı."
        elif komut == "sesi_ayarla" and seviye:
            muzik_ses_seviyesi = max(0, min(100, seviye))
            vlc_oynatici.audio_set_volume(muzik_ses_seviyesi)
            return f"Ses seviyesi yüzde {seviye} yapıldı."
        return "Müzik komutu anlaşılamadı."
    except Exception: return "Müzik ayarı yapılamadı."

groq_araclar = [
    {
        "type": "function",
        "function": {
            "name": "takvime_etkinlik_ekle",
            "description": "Takvime etkinlik ekler.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ozet": {"type": "string"},
                    "baslangic_zamani": {
                        "type": "string",
                        "description": "KESİNLİKLE 'YYYY-MM-DDTHH:MM:SS' formatında olmalı (Örn: 2026-05-06T15:00:00). Sadece tarih yetmez, mutlaka saati de ekle."
                    },
                    "bitis_zamani": {
                        "type": "string",
                        "description": "KESİNLİKLE 'YYYY-MM-DDTHH:MM:SS' formatında olmalı."
                    }
                },
                "required": ["ozet", "baslangic_zamani", "bitis_zamani"]
            }
        }
    },
    {"type": "function", "function": {"name": "takvim_listele", "description": "Yaklaşan takvim etkinliklerini söyler."}},
    {"type": "function", "function": {"name": "hava_durumu_sorgula", "description": "Hava durumu bilgisi.", "parameters": {"type": "object", "properties": {"sehir": {"type": "string", "description": "Sadece tek bir yalın ŞEHİR adı"}}, "required": ["sehir"]}}},
    {"type": "function", "function": {"name": "muzik_cal", "description": "Müzik açar.", "parameters": {"type": "object", "properties": {"sarki_adi": {"type": "string"}}, "required": ["sarki_adi"]}}},
    {"type": "function", "function": {"name": "muzik_kontrol", "description": "Müzik ses ve durum ayarı.", "parameters": {"type": "object", "properties": {"komut": {"type": "string", "enum": ["durdur", "devam", "kapat", "sesi_arttir", "sesi_azalt", "sesi_ayarla"]}, "seviye": {"type": "integer"}}, "required": ["komut"]}}}
]

# --- AKIŞLI (STREAMING) YAPAY ZEKA ---
def yapay_zekaya_sor_akisli(kullanici_metni):
    global mesaj_gecmisi
    mesaj_gecmisi.append({"role": "user", "content": kullanici_metni})
    hafiza_kontrolu()

    try:
        cevap_kontrol = groq_istemci.chat.completions.create(
            model="llama-3.3-70b-versatile", messages=mesaj_gecmisi, tools=groq_araclar, tool_choice="auto"
        )
        ai_mesaji = cevap_kontrol.choices[0].message

        if ai_mesaji.tool_calls:
            mesaj_gecmisi.append(ai_mesaji)
            for arac in ai_mesaji.tool_calls:
                fonksiyon_adi, parametreler = arac.function.name, json.loads(arac.function.arguments)
                if fonksiyon_adi == "hava_durumu_sorgula": sonuc = hava_durumu_sorgula(**parametreler)
                elif fonksiyon_adi == "takvime_etkinlik_ekle": sonuc = takvime_etkinlik_ekle(**parametreler)
                elif fonksiyon_adi == "muzik_cal": sonuc = muzik_cal(**parametreler)
                elif fonksiyon_adi == "muzik_kontrol": sonuc = muzik_kontrol(**parametreler)
                elif fonksiyon_adi == "takvim_listele": sonuc = takvim_listele()
                else: sonuc = "Bilinmeyen araç."
                mesaj_gecmisi.append({"tool_call_id": arac.id, "role": "tool", "name": fonksiyon_adi, "content": sonuc})

        akis_cevabi = groq_istemci.chat.completions.create(
            model="llama-3.3-70b-versatile", messages=mesaj_gecmisi, stream=True
        )

        tam_metin, gecici_cumle = "", ""
        for parca in akis_cevabi:
            if parca.choices[0].delta.content:
                icerik = parca.choices[0].delta.content
                gecici_cumle += icerik
                tam_metin += icerik

                if any(noktalama in icerik for noktalama in ['.', '!', '?', '\n']):
                    if gecici_cumle.strip(): yield gecici_cumle.strip()
                    gecici_cumle = ""

        if gecici_cumle.strip(): yield gecici_cumle.strip()
        mesaj_gecmisi.append({"role": "assistant", "content": tam_metin.strip()})
        hafiza_kontrolu()

    except Exception:
        yield "Bir bağlantı hatası oluştu."

# --- SES VE YANIT MEKANİZMASI ---
def ses_calma_iscisi():
    global asistan_konusuyor
    while True:
        cumle = ses_kuyrugu.get()
        if cumle == "SİSTEM_KAPAN": break
        asistan_konusuyor = True
        print(f"Argon: {cumle}", flush=True)
        try:
            dosya_adi = f"cevap_{threading.get_ident()}.mp3"
            asyncio.run(edge_tts.Communicate(cumle, "tr-TR-AhmetNeural").save(dosya_adi))
            pygame.mixer.music.load(dosya_adi)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy(): time.sleep(0.1)
            pygame.mixer.music.unload()
            if os.path.exists(dosya_adi): os.remove(dosya_adi)
        except: pass
        asistan_konusuyor = False
        ses_kuyrugu.task_done()

threading.Thread(target=ses_calma_iscisi, daemon=True).start()

def argonu_konustur(metin):
    for parca in re.split(r'(?<=[.!?]) +', metin):
        if parca.strip(): ses_kuyrugu.put(parca.strip())

def argonun_susmasini_bekle():
    time.sleep(0.2)
    while not ses_kuyrugu.empty() or asistan_konusuyor: time.sleep(0.1)

# --- HİBRİT KULAK ---
def uyanma_bekle():
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.dynamic_energy_threshold = False
        r.energy_threshold = 600

        print("\n[Uyku Modu] Bekleniyor ('Argon' seslen veya klavyeden yaz)...", flush=True)

        while True:
            if not metin_kuyrugu.empty(): return False

            argonun_susmasini_bekle()
            try:
                ses = r.listen(source, timeout=1, phrase_time_limit=3)
                metin = r.recognize_google(ses, language="tr-TR").lower()

                if any(k in metin for k in ["argon", "argan", "ergon"]):
                    muzik_sesini_ayarla(15)
                    return True
            except: pass

def dinle():
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.dynamic_energy_threshold = False
        r.energy_threshold = 600
        r.pause_threshold = 0.6
        print("\n[Aktif] Seni dinliyorum...", flush=True)
        try:
            ses = r.listen(source, timeout=5, phrase_time_limit=8)
            wav_byte_verisi = ses.get_wav_data(convert_rate=16000, convert_width=2)
            ceviri_sonucu = groq_istemci.audio.transcriptions.create(
                file=("ses.wav", wav_byte_verisi),
                model="whisper-large-v3",
                prompt="Argon, İstanbul, komut.",
                language="tr",
                response_format="text",
                temperature=0.0
            )
            return ceviri_sonucu.strip().lower()
        except: return ""

# --- ANA DÖNGÜ ---
def main():
    bellegi_temizle()
    argonu_konustur("Sistem devrede, ben Argon.")

    try:
        while True:
            kullanici_girdisi = ""

            if not metin_kuyrugu.empty():
                kullanici_girdisi = metin_kuyrugu.get()
                print(f"\nSen (Klavye): {kullanici_girdisi}", flush=True)
            else:
                uyandi_sesten = uyanma_bekle()

                if uyandi_sesten:
                    while not ses_kuyrugu.empty(): ses_kuyrugu.get_nowait()
                    pygame.mixer.music.stop()

                    argonu_konustur("Dinliyorum.")
                    argonun_susmasini_bekle()

                    if not metin_kuyrugu.empty():
                        kullanici_girdisi = metin_kuyrugu.get()
                        print(f"\nSen (Klavye): {kullanici_girdisi}", flush=True)
                    else:
                        kullanici_girdisi = dinle()
                        if kullanici_girdisi:
                            print(f"\nSen (Ses): {kullanici_girdisi}", flush=True)

            if kullanici_girdisi:
                if any(k in kullanici_girdisi.lower() for k in ["kapan", "çıkış"]):
                    argonu_konustur("Görüşürüz.")
                    time.sleep(1.5)
                    os.kill(os.getpid(), signal.SIGKILL)

                elif any(k in kullanici_girdisi for k in ["belleği temizle", "hafızayı sil"]):
                    bellegi_temizle()
                    argonu_konustur("Hafızamı sıfırladım, tertemiz bir sayfayla hazırım.")
                else:
                    for cumle in yapay_zekaya_sor_akisli(kullanici_girdisi):
                        if cumle: argonu_konustur(cumle)

            argonun_susmasini_bekle()
            muzik_sesini_ayarla(muzik_ses_seviyesi)

    except KeyboardInterrupt:
        os.kill(os.getpid(), signal.SIGKILL)

# --- KLAVYE İŞÇİSİ ---
def klavye_dinleyici():
    while True:
        try:
            girdi = input()
            if girdi.strip(): metin_kuyrugu.put(girdi.strip())
        except EOFError: break
threading.Thread(target=klavye_dinleyici, daemon=True).start()

if __name__ == "__main__":
    main()
