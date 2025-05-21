import os
import json
import html
import phonenumbers
from phonenumbers import NumberParseException, is_valid_number
from flask import Flask, request, Response, jsonify, send_from_directory
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore

# CONFIG
load_dotenv()
app = Flask(__name__)
twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_NUMBER")
base_url = os.getenv("BASE_URL")
client = Client(twilio_sid, twilio_token)
CONTACTS_FILE = "contacts.json"

# SCHEDULER
jobstores = {'default': MemoryJobStore()}
scheduler = BackgroundScheduler(jobstores=jobstores)

# FUNÇÕES AUXILIARES
def load_contacts():
    with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_contacts(data):
    with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def validar_numero(numero):
    try:
        parsed_number = phonenumbers.parse(numero, "BR")
        return is_valid_number(parsed_number)
    except NumberParseException:
        return False

# ROTAS FLASK
@app.route("/add-contact", methods=["POST"])
def add_contact():
    data = request.get_json()
    nome = data.get("nome", "").lower()
    telefone = data.get("telefone")
    contacts = load_contacts()
    contacts[nome] = telefone
    save_contacts(contacts)
    return jsonify({"status": "sucesso", "mensagem": f"{nome} salvo com sucesso."})

@app.route("/delete-contact", methods=["POST"])
def delete_contact():
    data = request.get_json()
    nome = data.get("nome", "").lower()
    contacts = load_contacts()
    if nome in contacts:
        del contacts[nome]
        save_contacts(contacts)
        return jsonify({"status": "sucesso", "mensagem": f"{nome} removido com sucesso."})
    else:
        return jsonify({"status": "erro", "mensagem": f"{nome} não encontrado."})

@app.route("/get-contacts")
def get_contacts():
    return jsonify(load_contacts())

@app.route("/painel-contatos.html")
def serve_painel():
    return send_from_directory(".", "painel-contatos.html")

@app.route("/verifica-sinal", methods=["POST"])
def verifica_sinal():
    resposta = request.form.get("SpeechResult", "").lower()
    tentativa = int(request.args.get("tentativa", 1))
    print(f"[RESPOSTA - Tentativa {tentativa}] {resposta}")

    if "protegido" in resposta:
        return _twiml_response("Entendido. Obrigado.", voice="Polly.Camila")
    elif tentativa < 2:
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
    else:
        contatos = load_contacts()
        numero_emergencia = contatos.get("emergencia")
        numero_falhou = request.values.get("To", "desconhecido")
        nome_falhou = next((nome for nome, tel in contatos.items() if tel == numero_falhou), None)

        if numero_emergencia and validar_numero(numero_emergencia):
            ligar_para_emergencia(numero_emergencia, numero_falhou, nome_falhou)
            return _twiml_response("Falha na confirmação. Chamando responsáveis.", voice="Polly.Camila")
        else:
            return _twiml_response("Erro ao tentar contatar emergência.", voice="Polly.Camila")

@app.route("/testar-verificacao/<nome>")
def testar_verificacao(nome):
    ligar_para_verificacao_por_nome(nome)
    return f"Ligação de verificação para {nome} iniciada."

# LIGAÇÕES
def ligar_para_verificacao(numero_destino):
    full_url = f"{base_url}/verifica-sinal?tentativa=1"
    client.calls.create(
        to=numero_destino,
        from_=twilio_number,
        twiml=f'''
        <Response>
            <Gather input="speech" timeout="5" speechTimeout="auto" action="{full_url}" method="POST" language="pt-BR">
                <Say voice="Polly.Camila" language="pt-BR">Central de monitoramento?</Say>
            </Gather>
            <Redirect method="POST">{full_url}</Redirect>
        </Response>
        '''
    )

def ligar_para_emergencia(numero_destino, origem_falha_numero=None, origem_falha_nome=None):
    if origem_falha_nome:
        mensagem = html.escape(f"{origem_falha_nome} não respondeu à verificação de segurança. Por favor, entre em contato.")
    elif origem_falha_numero:
        mensagem = html.escape(f"O número {origem_falha_numero} não respondeu à verificação de segurança. Por favor, entre em contato.")
    else:
        mensagem = html.escape("Alguém não respondeu à verificação de segurança. Por favor, entre em contato.")

    client.calls.create(
        to=numero_destino,
        from_=twilio_number,
        twiml=f'''
        <Response>
            <Say voice="Polly.Camila" language="pt-BR">{mensagem}</Say>
            <Say voice="Polly.Camila" language="pt-BR">Encerrando ligação.</Say>
        </Response>
        '''
    )

def ligar_para_verificacao_por_nome(nome):
    contatos = load_contacts()
    numero = contatos.get(nome)
    if numero:
        print(f"[AGENDAMENTO MANUAL] Ligando para {nome} - {numero}")
        ligar_para_verificacao(numero)

def _twiml_response(texto, voice="Polly.Camila"):
    resp = VoiceResponse()
    resp.say(texto, language="pt-BR", voice=voice)
    return Response(str(resp), mimetype="text/xml")

# AGENDA
def agendar_multiplas_ligacoes():
    agendamentos = [
        {"nome": "gustavo", "hora_inicio": 11, "hora_fim": 18, "minuto": 0},
        {"nome": "joão do posto 2", "hora": 11, "minuto": 12},  # ligação única
    ]
    for ag in agendamentos:
        if "hora_inicio" in ag and "hora_fim" in ag:
            for hora in range(ag["hora_inicio"], ag["hora_fim"] + 1):
                job_id = f"verificacao_{ag['nome']}_{hora}"
                if not scheduler.get_job(job_id):
                    scheduler.add_job(
                        lambda nome=ag["nome"]: ligar_para_verificacao_por_nome(nome),
                        'cron',
                        hour=hora,
                        minute=ag["minuto"],
                        id=job_id
                    )
        else:
            job_id = f"verificacao_{ag['nome']}_unica"
            if not scheduler.get_job(job_id):
                scheduler.add_job(
                    lambda nome=ag["nome"]: ligar_para_verificacao_por_nome(nome),
                    'cron',
                    hour=ag["hora"],
                    minute=ag["minuto"],
                    id=job_id
                )

# CHAMADA
agendar_multiplas_ligacoes()
scheduler.start()

# FLASK START
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
