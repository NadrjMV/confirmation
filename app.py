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

@app.route("/testar-verificacao/<nome>")
def testar_verificacao(nome):
    """Rota para testar a verificação de um contato."""
    ligar_para_verificacao_por_nome(nome)
    return f"Ligação de verificação para {nome} iniciada."

@app.route("/testar-emergencia")
def testar_emergencia():
    """Rota para testar uma chamada de emergência."""
    contatos = load_contacts()
    numero_emergencia = contatos.get("emergencia")
    if not numero_emergencia or not validar_numero(numero_emergencia):
        return "Número de emergência inválido.", 400
    
    ligar_para_emergencia(numero_emergencia, origem_falha_nome="Teste")
    return "Ligação de emergência disparada."

# FUNÇÕES DE LIGAÇÕES

def ligar_para_verificacao(numero_destino):
    """Inicia uma ligação de verificação para o número fornecido."""
    print(f"[LIGAÇÃO] Iniciando verificação para {numero_destino}")
    twiml_url = f"{base_url}/verifica-sinal?tentativa=1"
    try:
        client.calls.create(
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
        print(f"[LIGAÇÃO] Chamada para {numero_destino} iniciada.")
    except Exception as e:
        print(f"[ERRO] Falha ao fazer a ligação para {numero_destino}: {e}")

def ligar_para_emergencia(numero_destino, origem_falha_numero=None, origem_falha_nome=None):
    """Faz uma ligação para o número de emergência."""
    if origem_falha_nome:
        msg = f"{origem_falha_nome} não respondeu à verificação de segurança."
    elif origem_falha_numero:
        msg = f"O número {origem_falha_numero} não respondeu à verificação de segurança."
    else:
        msg = "Alguém não respondeu à verificação de segurança."

    print(f"[EMERGÊNCIA] Ligando para {numero_destino}: {msg}")
    twiml = f'''
    <Response>
        <Say voice="Polly.Camila" language="pt-BR">{msg}</Say>
        <Pause length="2"/>
        <Say voice="Polly.Camila" language="pt-BR">Encerrando ligação.</Say>
    </Response>
    '''
    try:
        client.calls.create(
            to=numero_destino,
            from_=twilio_number,
            twiml=twiml
        )
        print(f"[EMERGÊNCIA] Ligação para {numero_destino} enviada.")
    except Exception as e:
        print(f"[ERRO] Falha ao fazer a ligação para {numero_destino}: {e}")

# AGENDAMENTO DE LIGAÇÕES

def agendar_ligacoes():
    """Agendar as ligações de verificação para horários específicos."""
    agendamentos = [
        {"nome": "jordan", "hora": 8, "minuto": 11},
    ]
    for ag in agendamentos
