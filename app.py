import os
import json
import html
from flask import Flask, request, Response, jsonify, send_from_directory
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()
app = Flask(__name__)

twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_NUMBER")
base_url = os.getenv("BASE_URL")  # ← pega URL pública
client = Client(twilio_sid, twilio_token)

CONTACTS_FILE = "contacts.json"

def load_contacts():
    with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_contacts(data):
    with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
        print("Palavra correta detectada.")
        return _twiml_response("Entendido. Obrigado.", voice="Polly.Camila")
    elif tentativa < 2:
        print("Não entendi. Tentando novamente...")
        resp = VoiceResponse()
        gather = Gather(
            input="speech",
            timeout=5,
            speechTimeout="auto",
            action=f"{base_url}/verifica-sinal?tentativa={tentativa + 1}",
            method="POST",
            language="pt-BR"
        )
        gather.say("Não entendi. Fale novamente.", language="pt-BR", voice="Polly.Camila")
        resp.append(gather)
        resp.say("Encerrando ligação.", language="pt-BR", voice="Polly.Camila")
        return Response(str(resp), mimetype="text/xml")
    else:
        print("Nenhuma resposta válida. Ligando para emergência.")
        contatos = load_contacts()
        numero_emergencia = contatos.get("emergencia")
    if numero_emergencia:
        numero_falhou = request.values.get("From", "desconhecido")
        contatos = load_contacts()

        nome_falhou = next((nome for nome, tel in contatos.items() if tel == numero_falhou), None)

        ligar_para_emergencia(
            numero_destino=numero_emergencia,
            origem_falha_numero=numero_falhou,
            origem_falha_nome=nome_falhou
        )
        return _twiml_response("Falha na confirmação. Chamando responsáveis.", voice="Polly.Camila")

def ligar_para_verificacao(numero_destino):
    # VERIFICAÇÃO normal
    full_url = f"{base_url}/verifica-sinal?tentativa=1"
    client.calls.create(
        to=numero_destino,
        from_=twilio_number,
        twiml=f'''
        <Response>
            <Gather input="speech" timeout="5" speechTimeout="auto" action="{full_url}" method="POST" language="pt-BR">
                <Say voice="Polly.Camila" language="pt-BR">Central de monitoramento?</Say>
            </Gather>
            <Say voice="Polly.Camila" language="pt-BR">Encerrando ligação.</Say>
        </Response>
        '''
    )

def ligar_para_emergencia(numero_destino, origem_falha_numero=None, origem_falha_nome=None):
    # LIGAÇÃO DE EMERGÊNCIA
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

@app.route("/testar-verificacao/<nome>")
def testar_verificacao(nome):
    ligar_para_verificacao_por_nome(nome)
    return f"Ligação de verificação para {nome} iniciada."

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

def agendar_multiplas_ligacoes():
    agendamentos = [
        {"nome": "verificacao1", "hora": 8, "minuto": 0},
        {"nome": "verificacao2", "hora": 12, "minuto": 0},
        {"nome": "verificacao3", "hora": 18, "minuto": 0},
    ]
    for ag in agendamentos:
        scheduler.add_job(
            lambda nome=ag["nome"]: ligar_para_verificacao_por_nome(nome),
            'cron',
            hour=ag["hora"],
            minute=ag["minuto"],
            id=f"verificacao_{ag['nome']}"
        )

scheduler = BackgroundScheduler()
agendar_multiplas_ligacoes()
scheduler.start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
