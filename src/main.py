import machine
import time
import network
import urequests
import dht
import neopixel 

# ===========================================================
# CONFIGURAÇÕES
# ===========================================================
WIFI_SSID     = "Wokwi-GUEST"
WIFI_PASSWORD = ""
BOT_TOKEN     = ""
CHAT_ID       = ""
TELEGRAM_POLL_MS = 3000
INVASAO_LATCH_MS = 12000

LIMIAR_GAS_ALTO   = 55.0   
LIMIAR_GAS_SEGURO = 45.0   
LIMIAR_TEMP_ALTA  = 35.0   
LIMIAR_TEMP_BAIXA = 0.0    
LIMIAR_TEMP_OK_HI = 32.0   
LIMIAR_UMID_ALTA  = 80.0   
LIMIAR_UMID_BAIXA = 20.0   

PIR_DEBOUNCE_COUNT = 3
PIR_LATCH_MS = 5000

# ===========================================================
# MÁQUINA DE ESTADOS
# ===========================================================
ESTADO_NORMAL   = 0
ESTADO_INVASAO  = 1
ESTADO_GAS      = 2
ESTADO_TEMP     = 3
ESTADO_UMIDADE  = 4

estado_atual = ESTADO_NORMAL
last_update_id = 0
ultimo_poll_telegram = 0
invasao_ate_ms = 0
ultimo_evento_txt = "Nenhum evento critico recente"
ultimo_evento_ms = 0

# ===========================================================
# PINOS E COMPONENTES FÍSICOS
# ===========================================================
# Configuração do Buzzer para PWM (permite controle de frequência/tom)
buzzer = machine.PWM(machine.Pin(18))
buzzer.duty(0) # Inicia silenciado

led_green  = machine.Pin(16, machine.Pin.OUT)
led_red    = machine.Pin(17, machine.Pin.OUT)
led_yellow = machine.Pin(19, machine.Pin.OUT)

# Relé da Válvula de Gás
rele_gas   = machine.Pin(23, machine.Pin.OUT)
rele_gas.value(0) 

# Sensores
sensor_gas  = machine.ADC(machine.Pin(34))
sensor_gas.atten(machine.ADC.ATTN_11DB)
sensor_temp = dht.DHT22(machine.Pin(4))
sensor_pir  = machine.Pin(13, machine.Pin.IN)

# Anel de LEDs
NUM_LEDS = 16
ring = neopixel.NeoPixel(machine.Pin(22), NUM_LEDS)

# ===========================================================
# FUNÇÕES VISUAIS E SONORAS
# ===========================================================
def apagar_anel():
    for i in range(NUM_LEDS): ring[i] = (0, 0, 0)
    ring.write()

def ligar_anel_vermelho():
    for i in range(NUM_LEDS): ring[i] = (255, 0, 0)
    ring.write()

def bip_curto():
    """Som rápido para alertas menores ou inicialização"""
    buzzer.freq(1000) 
    buzzer.duty(10)  
    time.sleep_ms(150)
    buzzer.duty(0)    

def set_leds_painel(verde, vermelho, amarelo):
    """Controla os 3 LEDs da protoboard de uma vez"""
    led_green.value(verde)
    led_red.value(vermelho)
    led_yellow.value(amarelo)

pir_irq_pendente = False

def on_pir_rise(pin):
    # ISR minimalista: apenas marca evento para processamento no loop.
    global pir_irq_pendente
    pir_irq_pendente = True

sensor_pir.irq(trigger=machine.Pin.IRQ_RISING, handler=on_pir_rise)

# ===========================================================
# DRIVER LCD
# ===========================================================
class MiniLCD:
    def __init__(self, scl_pin, sda_pin, addr=0x27):
        self.i2c  = machine.SoftI2C(scl=machine.Pin(scl_pin), sda=machine.Pin(sda_pin), freq=100000)
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
        text = text[:20]
        while len(text) < 20: text += " "
        self._write(0x80 if line == 0 else 0xC0, 0)
        for c in text: self._write(ord(c), 1)

# ===========================================================
# TELEGRAM
# ===========================================================
def send_telegram(msg):
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    payload = '{"chat_id":"' + CHAT_ID + '","text":"' + msg.replace('"', "'") + '"}'
    try:
        r = urequests.post(url, data=payload.encode("utf-8"), headers={"Content-Type": "application/json"})
        r.close()
    except Exception as e:
        print("[Telegram] Erro:", e)

def nome_estado(estado):
    if estado == ESTADO_NORMAL:
        return "NORMAL"
    if estado == ESTADO_INVASAO:
        return "INVASAO"
    if estado == ESTADO_GAS:
        return "GAS"
    if estado == ESTADO_TEMP:
        return "TEMPERATURA"
    if estado == ESTADO_UMIDADE:
        return "UMIDADE"
    return "DESCONHECIDO"

def registrar_evento(texto, tempo_atual):
    global ultimo_evento_txt, ultimo_evento_ms
    ultimo_evento_txt = texto
    ultimo_evento_ms = tempo_atual

def montar_status(gas, temp, umid, movimento, tempo_atual):
    wifi_status = "OK" if wlan.isconnected() else "OFF"
    mov_status = "SIM" if movimento else "NAO"
    if ultimo_evento_ms == 0:
        evento_str = ultimo_evento_txt
    else:
        segundos = int(time.ticks_diff(tempo_atual, ultimo_evento_ms) / 1000)
        if segundos < 0:
            segundos = 0
        evento_str = ultimo_evento_txt + " (ha " + str(segundos) + "s)"

    return (
        "📊 STATUS\n"
        + "Estado: " + nome_estado(estado_atual) + "\n"
        + "Gas: " + str(int(gas)) + "%\n"
        + "Temp: " + str(round(temp, 1)) + "C\n"
        + "Umid: " + str(int(umid)) + "%\n"
        + "Movimento: " + mov_status + "\n"
        + "WiFi: " + wifi_status + "\n"
        + "Ultimo evento: " + evento_str
    )

def processar_comandos_telegram(gas, temp, umid, movimento, tempo_atual):
    global last_update_id

    url = (
        "https://api.telegram.org/bot"
        + BOT_TOKEN
        + "/getUpdates?offset="
        + str(last_update_id + 1)
        + "&timeout=0"
    )

    try:
        r = urequests.get(url)
        data = r.json()
        r.close()
    except Exception as e:
        print("[Telegram] Erro no polling:", e)
        return

    if not data.get("ok"):
        return

    for update in data.get("result", []):
        update_id = update.get("update_id", 0)
        if update_id > last_update_id:
            last_update_id = update_id

        msg = update.get("message") or update.get("edited_message")
        if not msg:
            continue

        chat_id_msg = str(msg.get("chat", {}).get("id", ""))
        if chat_id_msg != CHAT_ID:
            continue

        texto = (msg.get("text") or "").strip()
        if not texto:
            continue

        cmd = texto.upper()
        if cmd == "STATUS" or cmd.startswith("/STATUS"):
            send_telegram(montar_status(gas, temp, umid, movimento, tempo_atual))

# ===========================================================
# BOOT DA CENTRAL
# ===========================================================
lcd = MiniLCD(scl_pin=32, sda_pin=33)
lcd.puts("Conectando WiFi", 0)

set_leds_painel(0, 0, 1) 

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(WIFI_SSID, WIFI_PASSWORD)

while not wlan.isconnected():
    time.sleep_ms(500)

print("Teste")
print("Conectado ao WiFi!")


lcd.puts("WiFi OK!", 0)
set_leds_painel(1, 0, 0) # Verde quando conecta
send_telegram("🚀 Central Iniciada! [Painel, Sirene e Valvula OK]")
send_telegram("💬 Envie STATUS para consultar o estado atual da central.")
time.sleep_ms(1000)
lcd.clear()

# ===========================================================
# VARIÁVEIS DE LOOP
# ===========================================================
temperatura_atual  = 0.0
umidade_atual      = 0.0
ultima_leitura_dht = 0
pir_contador       = 0
pir_confirmado     = False
pir_detectado_ate_ms = 0

sirene_toggle      = False 

# ===========================================================
# LOOP PRINCIPAL
# ===========================================================
while True:
    tempo_atual = time.ticks_ms()

    # 1. DHT22
    if time.ticks_diff(tempo_atual, ultima_leitura_dht) >= 2000:
        ultima_leitura_dht = tempo_atual
        try:
            sensor_temp.measure()
            temperatura_atual = sensor_temp.temperature()
            umidade_atual     = sensor_temp.humidity()
        except OSError: pass

    # 2. Gás
    soma = 0
    for _ in range(30):
        soma += sensor_gas.read()
        time.sleep_ms(1)
    gas = (soma / 30.0 * 100.0) / 4095.0
    if gas < 1.0: gas = 0.0

    # 3. Debounce PIR
    pir_detectado_agora = False

    if pir_irq_pendente:
        pir_irq_pendente = False
        pir_detectado_ate_ms = time.ticks_add(tempo_atual, PIR_LATCH_MS)
        pir_detectado_agora = True

    if sensor_pir.value() == 1:
        pir_contador += 1
        pir_detectado_ate_ms = time.ticks_add(tempo_atual, PIR_LATCH_MS)
        if pir_contador >= PIR_DEBOUNCE_COUNT:
            pir_detectado_agora = True
    else:
        pir_contador = 0

    pir_confirmado = (
        pir_detectado_agora
        or time.ticks_diff(pir_detectado_ate_ms, tempo_atual) > 0
    )

    # Latch de invasao so renova quando existe deteccao nova real.
    if pir_detectado_agora:
        invasao_ate_ms = time.ticks_add(tempo_atual, INVASAO_LATCH_MS)

    invasao_latch_ativo = time.ticks_diff(invasao_ate_ms, tempo_atual) > 0

    # 4. Atualiza LCD
    t_str = str(temperatura_atual)[:4]
    lcd.puts(f"G:{int(gas)}% T:{t_str}C U:{int(umidade_atual)}%", 0)

    # 5. Máquina de estados
    novo_estado = estado_atual

    if gas > LIMIAR_GAS_ALTO:
        novo_estado = ESTADO_GAS
    elif temperatura_atual < LIMIAR_TEMP_BAIXA or temperatura_atual > LIMIAR_TEMP_ALTA:
        novo_estado = ESTADO_TEMP
    elif pir_confirmado or invasao_latch_ativo:
        novo_estado = ESTADO_INVASAO
    elif umidade_atual > LIMIAR_UMID_ALTA or umidade_atual < LIMIAR_UMID_BAIXA:
        novo_estado = ESTADO_UMIDADE
    elif (gas < LIMIAR_GAS_SEGURO
          and LIMIAR_TEMP_BAIXA <= temperatura_atual <= LIMIAR_TEMP_OK_HI
          and LIMIAR_UMID_BAIXA <= umidade_atual <= LIMIAR_UMID_ALTA
          and not pir_confirmado):
        novo_estado = ESTADO_NORMAL

    # --- TRANSIÇÕES (Executam 1x quando muda de estado) ---
    if novo_estado != estado_atual:
        estado_anterior = estado_atual
        estado_atual    = novo_estado

        if estado_atual == ESTADO_GAS:
            rele_gas.value(1) 
            set_leds_painel(0, 1, 0)
            ligar_anel_vermelho()
            lcd.puts("GAS: VALVULA FECHADA", 1)
            registrar_evento("GAS CRITICO", tempo_atual)
            send_telegram(f"⛔ CRITICO: Vazamento de gas ({int(gas)}%)!")

        elif estado_atual == ESTADO_TEMP:
            set_leds_painel(0, 1, 0)
            if temperatura_atual > LIMIAR_TEMP_ALTA:
                lcd.puts(f"CALOR: {t_str}C", 1)
                registrar_evento("TEMPERATURA ALTA", tempo_atual)
                send_telegram(f"🔥 ALERTA: Temperatura alta! {t_str}C")
            else:
                lcd.puts(f"FRIO: {t_str}C", 1)
                registrar_evento("TEMPERATURA BAIXA", tempo_atual)
                send_telegram(f"❄️ ALERTA: Temperatura muito baixa! {t_str}C")

        elif estado_atual == ESTADO_INVASAO:
            set_leds_painel(0, 1, 0) 
            ligar_anel_vermelho()
            lcd.puts("!! MOVIMENTO !!!", 1)
            registrar_evento("INVASAO DETECTADA", tempo_atual)
            send_telegram("🚨 ALERTA: Invasor detectado! Sirene ativada.")

        elif estado_atual == ESTADO_UMIDADE:
            set_leds_painel(0, 0, 1) 
            bip_curto()              
            if umidade_atual > LIMIAR_UMID_ALTA:
                lcd.puts(f"UMIDADE ALTA: {int(umidade_atual)}%", 1)
                registrar_evento("UMIDADE ALTA", tempo_atual)
            else:
                lcd.puts(f"UMID BAIXA: {int(umidade_atual)}%", 1)
                registrar_evento("UMIDADE BAIXA", tempo_atual)

        elif estado_atual == ESTADO_NORMAL:
            rele_gas.value(0)
            apagar_anel()
            set_leds_painel(1, 0, 0) 
            buzzer.duty(0)           
            bip_curto()             
            lcd.puts("Sistema Normal", 1)
            if estado_anterior != ESTADO_NORMAL:
                send_telegram("✅ Sistema normalizado.")

    # --- AÇÕES CONTÍNUAS (A mágica da Sirene Pulsante) ---
    if estado_atual == ESTADO_INVASAO or estado_atual == ESTADO_GAS:
        # Alterna a variável entre True/False a cada ciclo do loop
        sirene_toggle = not sirene_toggle 
        if sirene_toggle:
            buzzer.freq(200) 
            buzzer.duty(1)
        else:
            buzzer.freq(100) 
            buzzer.duty(1)
    
    # Tempo do loop
    if time.ticks_diff(tempo_atual, ultimo_poll_telegram) >= TELEGRAM_POLL_MS:
        ultimo_poll_telegram = tempo_atual
        processar_comandos_telegram(gas, temperatura_atual, umidade_atual, pir_confirmado, tempo_atual)

    time.sleep_ms(150)