import machine
import time
import network
import urequests
import dht
import sys  

# Configurações de Rede e Telegram
WIFI_SSID = "Wokwi-GUEST"
WIFI_PASSWORD = ""
BOT_TOKEN = "xxxx"
CHAT_ID = "xxxx"

# Configuração dos Pinos de Saída
buzzer = machine.Pin(18, machine.Pin.OUT)
led_green = machine.Pin(16, machine.Pin.OUT)
led_red = machine.Pin(17, machine.Pin.OUT)
fan = machine.Pin(5, machine.Pin.OUT)

# Sensores
sensor_gas = machine.ADC(machine.Pin(34))
sensor_gas.atten(machine.ADC.ATTN_11DB)
sensor_temp = dht.DHT22(machine.Pin(4)) 
sensor_pir = machine.Pin(13, machine.Pin.IN)

# --- CLASSE DO LCD ---
class MiniLCD:
    def __init__(self, scl_pin, sda_pin, addr=0x27):
        self.i2c = machine.SoftI2C(scl=machine.Pin(scl_pin), sda=machine.Pin(sda_pin), freq=100000)
        self.addr = addr
        time.sleep_ms(50)
        for cmd in (0x33, 0x32, 0x28, 0x0C, 0x06, 0x01):
            self._write(cmd, 0)
            time.sleep_ms(2)

    def _write(self, val, mode):
        h = mode | (val & 0xF0) | 0x08
        l = mode | ((val << 4) & 0xF0) | 0x08
        self.i2c.writeto(self.addr, bytes([h | 0x04, h & ~0x04, l | 0x04, l & ~0x04]))

    def clear(self):
        self._write(0x01, 0)
        time.sleep_ms(2)

    def puts(self, text, line=0):
        self._write(0x80 if line == 0 else 0xC0, 0)
        for c in text:
            self._write(ord(c), 1)

# --- FUNÇÃO DE ENVIAR MENSAGEM DO TELEGRAM ---
def send_telegram_msg(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = '{"chat_id": "' + CHAT_ID + '", "text": "' + msg + '"}'
    headers = {'Content-Type': 'application/json'}
    try:
        r = urequests.post(url, data=payload.encode('utf-8'), headers=headers)
        r.close()
    except Exception as e:
        print("\n[Telegram] Erro ao enviar:", e)

# --- SETUP INICIAL ---
lcd = MiniLCD(scl_pin=22, sda_pin=21)
lcd.puts("Conectando...", 0)

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(WIFI_SSID, WIFI_PASSWORD)

while not wlan.isconnected():
    time.sleep_ms(500)
    print(".", end="")

print("Teste")
print("Conectado ao WiFi!")

lcd.clear()
lcd.puts("Sistema Pronto!", 0)
send_telegram_msg("✅ Monitoramento Online: Gas, Temp e Movimento!")

# --- VARIÁVEIS DE ESTADO E TEMPO ---
perigo_gas = False
perigo_temp = False
perigo_invasao = False
ultima_leitura_dht = 0
temperatura_atual = 0.0

# --- LOOP PRINCIPAL ---
while True:
    tempo_atual = time.ticks_ms()

    # 1. LEITURA DA TEMPERATURA (A cada 2 segundos)
    if time.ticks_diff(tempo_atual, ultima_leitura_dht) > 2000:
        ultima_leitura_dht = tempo_atual
        try:
            sensor_temp.measure()
            temperatura_atual = sensor_temp.temperature()
        except OSError:
            pass 

    # 2. LEITURA DO GÁS
    soma = 0
    for _ in range(30):
        soma += sensor_gas.read()
        time.sleep_ms(2)
    leitura_media = soma / 30.0
    gas = (leitura_media * 100.0) / 4095.0
    if gas < 1.0: gas = 0.0

    # 3. ATUALIZA A TELA SUPERIOR
    lcd.puts(f"G:{gas:.0f}% T:{temperatura_atual:.1f}C    ", 0)

    # 4. LÓGICA DE ALARMES

    # --- CENÁRIO: MOVIMENTO (PRIORIDADE) ---
    if sensor_pir.value() == 1:
        if not perigo_invasao:
            perigo_invasao = True
            buzzer.value(1)
            led_red.value(1)
            led_green.value(0)
            lcd.puts("MOVIMENTO!        ", 1)
            send_telegram_msg("🚨 MOVIMENTO DETECTADO NO AMBIENTE!")
    
    # --- CENÁRIO A: VAZAMENTO DE GÁS ---
    elif gas > 55.0:
        if not perigo_gas:
            perigo_gas = True
            buzzer.value(1)
            led_red.value(1)
            led_green.value(0)
            fan.value(1)
            lcd.puts("ALERTA: GAS!      ", 1)
            send_telegram_msg(f"⚠️ VAZAMENTO DE GAS: {gas:.1f}%")

    # --- CENÁRIO B: TEMPERATURA NEGATIVA ---
    elif temperatura_atual < 0.0:
        if not perigo_temp:
            perigo_temp = True
            led_red.value(1)
            led_green.value(0)
            lcd.puts("ERRO: TEMP < 0C   ", 1)
            send_telegram_msg(f"❄️ ALERTA CRITICO: Temperatura negativa! ({temperatura_atual:.1f}C)")

    # --- CENÁRIO C: CALOR EXCESSIVO ---
    elif temperatura_atual > 35.0:
        if not perigo_temp:
            perigo_temp = True
            led_red.value(1)
            led_green.value(0)
            fan.value(1) 
            lcd.puts("ALERTA: CALOR!    ", 1)
            send_telegram_msg(f"🔥 ALERTA DE CALOR: {temperatura_atual:.1f}C")

    # --- CENÁRIO D: TUDO NORMAL ---
    elif gas < 45.0 and 0.0 <= temperatura_atual <= 32.0 and sensor_pir.value() == 0:
        if perigo_gas or perigo_temp or perigo_invasao:
            perigo_gas = False
            perigo_temp = False
            perigo_invasao = False
            buzzer.value(0)
            led_red.value(0)
            led_green.value(1)
            fan.value(0)
            lcd.puts("Sistema Normal    ", 1)
            send_telegram_msg("✅ Condições normalizadas.")

    time.sleep_ms(200)
