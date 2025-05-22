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
from datetime import datetime
from pytz import timezone  # ✅ Importa o fuso horário

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

@app.route("/verifica-sinal", methods=["GET", "POST"])
def verifica_sinal():
    resposta = request.form.get("SpeechResult", "").lower()
    tentativa = int(request.args.get("tentativa", 1))
    print(f"[RESPOSTA - Tentativa {tentativa}] {resposta}")

    if "protegido" in resposta:
        print("[SUCESSO] Palavra correta detectada.")
        return _twiml_response("Entendido. Obrigado.", voice="Polly.Camila")

    if tentativa < 2:
        print("[TENTATIVA FALHOU] Repetindo verificação...")
        resp = VoiceResponse()
        gather = Gather(
            input="speech",
            timeout=5,
            speechTimeout="auto",
            action=f"{base_url}/verifica-sinal?tentativa={tentativa + 1}",
            method="POST",
            language="pt-BR"
        )
        gather.say("Contra-senha incorreta. Fale novamente.", language="pt-BR", voice="Polly.Camila")
        resp.append(gather)
        resp.redirect(f"{base_url}/verifica-sinal?tentativa={tentativa + 1}", method="POST")
        return Response(str(resp), mimetype="text/xml")

    print("[FALHA TOTAL] Chamando número de emergência...")
    contatos = load_contacts()
    numero_emergencia = contatos.get("emergencia")

    # 💡 Captura corretamente o número de quem falhou
    numero_falhou = request.values.get("To", None)
    nome_falhou = None
    if numero_falhou:
        for nome, tel in contatos.items():
            if tel == numero_falhou:
                nome_falhou = nome
                break

    print(f"[DEBUG] Número que falhou: {numero_falhou}")
    print(f"[DEBUG] Nome correspondente: {nome_falhou}")
    print(f"[DEBUG] Número emergência: {numero_emergencia}")

    if numero_emergencia and validar_numero(numero_emergencia):
        ligar_para_emergencia(
            numero_destino=numero_emergencia,
            origem_falha_numero=numero_falhou,
            origem_falha_nome=nome_falhou
        )
        return _twiml_response("Falha na confirmação. Chamando responsáveis.", voice="Polly.Camila")
    else:
        print("[ERRO] Número de emergência não encontrado ou inválido.")
        return _twiml_response("Erro ao tentar contatar emergência. Verifique os números cadastrados.", voice="Polly.Camila")

def ligar_para_verificacao(numero_destino):
    full_url = f"{base_url}/verifica-sinal?tentativa=1"
    response = VoiceResponse()
    gather = Gather(
        input="speech",
        timeout=5,
        speechTimeout="auto",
        action=full_url,
        method="POST",
        language="pt-BR"
    )
    gather.say("Central de monitoramento?", language="pt-BR", voice="Polly.Camila")
    response.append(gather)
    response.redirect(full_url, method="POST")

    client.calls.create(
        to=numero_destino,
        from_=twilio_number,
        twiml=response
    )

def validar_numero(numero):
    try:
        parsed = phonenumbers.parse(numero, "BR")
        return is_valid_number(parsed)
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
            <Say voice="Polly.Camila" language="pt-BR">{html.escape(mensagem)}</Say>
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
    numero = contatos.get(nome.lower())
    if numero and validar_numero(numero):
        print(f"[AGENDAMENTO] Ligando para {nome}: {numero}")
        ligar_para_verificacao(numero)
    else:
        print(f"[ERRO] Contato '{nome}' não encontrado ou inválido.")

def _twiml_response(texto, voice="Polly.Camila"):
    resp = VoiceResponse()
    resp.say(texto, language="pt-BR", voice=voice)
    return Response(str(resp), mimetype="text/xml")

# ✅ Scheduler com fuso horário brasileiro
scheduler = BackgroundScheduler(timezone=timezone("America/Sao_Paulo"))

@app.route("/agendar-unica", methods=["POST"])
def agendar_unica():
    data = request.get_json()
    nome = data.get("nome")
    hora = int(data.get("hora"))
    minuto = int(data.get("minuto"))

    job_id = f"teste_{nome}_{hora}_{minuto}"
    scheduler.add_job(
        func=lambda: ligar_para_verificacao_por_nome(nome),
        trigger="cron",
        hour=hora,
        minute=minuto,
        id=job_id,
        replace_existing=True
    )

    return jsonify({"status": "ok", "mensagem": f"Ligação para {nome} agendada às {hora:02d}:{minuto:02d}"})

def agendar_multiplas_ligacoes():
    agendamentos = [
        {"nome": "verificacao1", "hora": datetime.now().hour, "minuto": (datetime.now().minute + 1) % 60},
    ]
    for ag in agendamentos:
        scheduler.add_job(
            func=lambda nome=ag["nome"]: ligar_para_verificacao_por_nome(nome),
            trigger="cron",
            hour=ag["hora"],
            minute=ag["minuto"],
            id=f"verificacao_{ag['nome']}",
            replace_existing=True
        )

def agendar_ligacoes_fixas():
    ligacoes = [
        {"nome": "fk", "hora": 9, "minuto": 22},
    ]
    for i, item in enumerate(ligacoes):
        scheduler.add_job(
            func=lambda nome=item["nome"]: ligar_para_verificacao_por_nome(nome),
            trigger="cron",
            hour=item["hora"],
            minute=item["minuto"],
            id=f"ligacao_fixa_{i}",
            replace_existing=True
        )

agendar_ligacoes_fixas()

ligacoes = {
    "verificacao1": [(10, 00), (11, 00), (12, 00), (13, 00), (14, 00), (15, 00)],
    "verificacao2": [(11, 30)]
}
for nome, horarios in ligacoes.items():
    for i, (hora, minuto) in enumerate(horarios):
        scheduler.add_job(
            func=lambda nome=nome: ligar_para_verificacao_por_nome(nome),
            trigger="cron",
            hour=hora,
            minute=minuto,
            id=f"{nome}_{hora}_{minuto}",
            replace_existing=True
        )

agendar_multiplas_ligacoes()
scheduler.start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
