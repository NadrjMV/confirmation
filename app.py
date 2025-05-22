import os
import json
import phonenumbers
from phonenumbers import NumberParseException, is_valid_number
from flask import Flask, request, Response, jsonify, send_from_directory
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from datetime import datetime

# CONFIGURAÇÕES INICIAIS
load_dotenv()
app = Flask(__name__)

twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_NUMBER")
base_url = os.getenv("BASE_URL") 
CONTACTS_FILE = "contacts.json"

# Instanciando o cliente Twilio
client = Client(twilio_sid, twilio_token)

# AGENDADOR
jobstores = {'default': MemoryJobStore()}
scheduler = BackgroundScheduler(jobstores=jobstores)

# FUNÇÕES AUXILIARES
def load_contacts():
    """Carregar os contatos do arquivo JSON."""
    if not os.path.exists(CONTACTS_FILE):
        return {}
    with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_contacts(data):
    """Salvar os contatos no arquivo JSON."""
    with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def validar_numero(numero):
    """Validar se o número de telefone é válido no Brasil."""
    try:
        parsed_number = phonenumbers.parse(numero, "BR")
        return is_valid_number(parsed_number)
    except NumberParseException:
        return False

def _twiml_response(texto, voice="Polly.Camila"):
    """Gera a resposta Twilio com a mensagem falada."""
    resp = VoiceResponse()
    resp.say(texto, language="pt-BR", voice=voice)
    return Response(str(resp), mimetype="text/xml")

def ligar_para_verificacao(numero_destino):
    """Iniciar uma ligação para verificação."""
    print(f"[DEBUG] Ligando para {numero_destino}")
    twiml_url = f"{base_url}/verifica-sinal?tentativa=1"
    
    try:
        call = client.calls.create(
            to=numero_destino,
            from_=twilio_number,
            twiml=f'''
            <Response>
                <Gather input="speech" timeout="5" speechTimeout="auto" action="{twiml_url}" method="POST" language="pt-BR">
                    <Say voice="Polly.Camila" language="pt-BR">Central de monitoramento?</Say>
                </Gather>
                <Redirect method="POST">{twiml_url}</Redirect>
            </Response>
            '''
        )
        print(f"[DEBUG] Ligação disparada. Call SID: {call.sid}")
    except Exception as e:
        print(f"[ERROR] Erro ao tentar fazer a ligação: {e}")

# ROTEAMENTO FLASK

@app.route("/add-contact", methods=["POST"])
def add_contact():
    """Rota para adicionar um contato."""
    data = request.get_json()
    nome = data.get("nome", "").lower()
    telefone = data.get("telefone")

    if not telefone or not validar_numero(telefone):
        return jsonify({"status": "erro", "mensagem": "Número inválido ou ausente."})

    contatos = load_contacts()
    contatos[nome] = telefone
    save_contacts(contatos)

    return jsonify({"status": "sucesso", "mensagem": f"Contato {nome} salvo com sucesso."})

@app.route("/delete-contact", methods=["POST"])
def delete_contact():
    """Rota para excluir um contato."""
    data = request.get_json()
    nome = data.get("nome", "").lower()

    contatos = load_contacts()
    if nome in contatos:
        del contatos[nome]
        save_contacts(contatos)
        return jsonify({"status": "sucesso", "mensagem": f"{nome} removido com sucesso."})
    
    return jsonify({"status": "erro", "mensagem": "Contato não encontrado."})

@app.route("/get-contacts", methods=["GET"])
def get_contacts():
    """Rota para obter todos os contatos."""
    return jsonify(load_contacts())

@app.route("/painel-contatos.html")
def painel():
    """Rota para retornar a página do painel de contatos."""
    return send_from_directory(".", "painel-contatos.html")

@app.route("/verifica-sinal", methods=["POST"])
def verifica_sinal():
    """Verificação de sinal por voz, utilizada para validação de segurança."""
    resposta = request.form.get("SpeechResult", "").lower()
    tentativa = int(request.args.get("tentativa", 1))
    print(f"[RESPOSTA - Tentativa {tentativa}] {resposta}")

    if "protegido" in resposta:
        return _twiml_response("Entendido. Obrigado.")
    
    if tentativa < 2:
        resp = VoiceResponse()
        gather = Gather(
            input="speech",
            timeout=5,
            speechTimeout="auto",
            action=f"{base_url}/verifica-sinal?tentativa={tentativa + 1}",
            method="POST",
            language="pt-BR"
        )
        gather.say("Contra senha incorreta. Fale novamente.", language="pt-BR", voice="Polly.Camila")
        resp.append(gather)
        resp.redirect(f"{base_url}/verifica-sinal?tentativa={tentativa + 1}", method="POST")
        return Response(str(resp), mimetype="text/xml")
    
    # Caso falhe na verificação, chama a emergência
    contatos = load_contacts()
    numero_emergencia = contatos.get("emergencia")
    numero_falhou = request.values.get("To", "")
    nome_falhou = next((nome for nome, tel in contatos.items() if tel == numero_falhou), None)

    if numero_emergencia and validar_numero(numero_emergencia):
        ligar_para_emergencia(numero_emergencia, numero_falhou, nome_falhou)
        return _twiml_response("Falha na verificação. Chamando emergência.")
    
    return _twiml_response("Erro ao chamar emergência.")

# AGENDAMENTO DE LIGAÇÕES

def agendar_ligacoes():
    """Agendar as ligações de verificação para horários específicos."""
    agendamentos = [
        {"nome": "jordan", "hora": 8, "minuto": 17},
        {"nome": "ana", "hora": 15, "minuto": 30},
    ]
    
    contatos = load_contacts()

    for ag in agendamentos:
        nome = ag['nome']
        hora = ag['hora']
        minuto = ag['minuto']
        print(f"[DEBUG] Agendando ligação para {nome} às {hora}:{minuto}.")
        
        job = scheduler.add_job(
            ligar_para_verificacao,
            'cron',
            hour=hora,
            minute=minuto,
            args=[contatos[nome]]
        )
        print(f"[DEBUG] Agendado para ligar para {nome} às {hora}:{minuto}.")

# INICIALIZANDO O SCHEDULER E O FLASK

if __name__ == "__main__":
    agendar_ligacoes()  # Agendar as ligações
    scheduler.start()  # Iniciar o agendador
    port = int(os.environ.get("PORT", 5000))  # Usar a porta do ambiente, ou 5000 se não for definida
    app.run(host="0.0.0.0", port=port)
