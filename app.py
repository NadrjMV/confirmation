import os
import json
import phonenumbers
from phonenumbers import NumberParseException, is_valid_number
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
base_url = os.getenv("BASE_URL")
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
        # Adiciona Redirect mesmo se não houver resposta
        resp.redirect(f"{base_url}/verifica-sinal?tentativa={tentativa + 1}", method="POST")
        return Response(str(resp), mimetype="text/xml")
    else:
        print("Nenhuma resposta válida. Ligando para emergência.")
        contatos = load_contacts()
        numero_emergencia = contatos.get("emergencia")

        if numero_emergencia and validar_numero(numero_emergencia):
            numero_falhou = request.values.get("To", "desconhecido")
            nome_falhou = next((nome for nome, tel in contatos.items() if tel == numero_falhou), None)

            ligar_para_emergencia(
                numero_destino=numero_emergencia,
                origem_falha_numero=numero_falhou,
                origem_falha_nome=nome_falhou
            )
            return _twiml_response("Falha na confirmação. Chamando responsáveis.", voice="Polly.Camila")
        else:
            print("Erro: número de emergência inválido ou não disponível.")
            return _twiml_response("Erro ao tentar contatar emergência. Verifique os números cadastrados.", voice="Polly.Camila")

def ligar_para_verificacao(numero_destino):
    print(f"[DEBUG] Ligando para {numero_destino}")
    full_url = f"{base_url}/verifica-sinal?tentativa=1"
    try:
        client.calls.create(
            to=numero_destino,
            from_=twilio_number,
            twiml=f'''
            <Response>
                <Gather input="speech"
                        timeout="5"
                        speechTimeout="auto"
                        action="{full_url}"
                        method="POST"
                        language="pt-BR">
                    <Say voice="Polly.Camila" language="pt-BR">Central de monitoramento?</Say>
                </Gather>
                <Redirect method="POST">{full_url}</Redirect>
            </Response>
            '''
        )
        print(f"[DEBUG] Ligação disparada para {numero_destino}.")
    except Exception as e:
        print(f"[ERROR] Erro ao tentar fazer a ligação para {numero_destino}: {e}")

def validar_numero(numero):
    try:
        parsed_number = phonenumbers.parse(numero, "BR")
        return is_valid_number(parsed_number)
    except NumberParseException:
        return False

def ligar_para_emergencia(numero_destino, origem_falha_numero=None, origem_falha_nome=None):
    if origem_falha_nome:
        mensagem = f"{origem_falha_nome} não respondeu à verificação de segurança. Por favor, entre em contato."
    elif origem_falha_numero:
        mensagem = f"O número {origem_falha_numero} não respondeu à verificação de segurança. Por favor, entre em contato."
    else:
        mensagem = "Alguém não respondeu à verificação de segurança. Por favor, entre em contato."

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
        {"nome": "jordan", "hora": 8, "minuto": 30},
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

# Start the scheduler
scheduler.start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)  # Ensure debug is set to False for production
