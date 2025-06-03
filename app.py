import os
import json
import html
import phonenumbers
from phonenumbers import NumberParseException, is_valid_number
from flask import Flask, request, Response, jsonify, send_from_directory
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from dotenv import load_dotenv
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from pytz import timezone

load_dotenv()
app = Flask(__name__)

twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_NUMBER")
base_url = os.getenv("BASE_URL")
client = Client(twilio_sid, twilio_token)

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
sg = SendGridAPIClient(SENDGRID_API_KEY)

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
        return _twiml_response("Entendido. Obrigado.", voice="alice")

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
        gather.say("Contra senha incorreta. Fale novamente.", language="pt-BR", voice="alice")
        resp.append(gather)
        resp.redirect(f"{base_url}/verifica-sinal?tentativa={tentativa + 1}", method="POST")
        return Response(str(resp), mimetype="text/xml")

    print("[FALHA TOTAL] Enviando e-mail para emergência...")
    contatos = load_contacts()
    numero_emergencia = contatos.get("emergencia")
    email_emergencia = contatos.get("email_emergencia")

    nome_falhou = None
    if numero_emergencia:
        for nome, tel in contatos.items():
            if tel == numero_emergencia:
                nome_falhou = nome
                break

    print(f"[DEBUG] Número que falhou: {numero_emergencia}")
    print(f"[DEBUG] Nome correspondente: {nome_falhou}")
    print(f"[DEBUG] E-mail emergência: {email_emergencia}")

    if email_emergencia:
        respostas_obtidas = resposta  # Resposta obtida durante a verificação
        enviar_email_emergencia(
            email_destino=email_emergencia,
            nome=nome_falhou,
            respostas_obtidas=respostas_obtidas
        )
        return _twiml_response("Falha na confirmação. Mensagem de emergência enviada por e-mail.", voice="alice")
    else:
        print("[ERRO] E-mail de emergência não encontrado ou inválido.")
        return _twiml_response("Erro ao tentar contatar emergência. Verifique os números cadastrados.", voice="alice")

def enviar_email_emergencia(email_destino, nome, respostas_obtidas):
    mensagem = f"Verificação do {nome} não correspondeu. Respostas obtidas: {respostas_obtidas}. Favor verificar."

    mail = Mail(
        from_email="desenvolvimento@sunshield.com.br",
        to_emails=email_destino,
        subject="Alerta de Verificação de Segurança",
        plain_text_content=mensagem
    )
    try:
        response = sg.send(mail)
        print(f"Email enviado para {email_destino}: {mensagem}")
        return response
    except Exception as e:
        print(f"[ERRO] Falha ao enviar o e-mail: {str(e)}")

def validar_numero(numero):
    try:
        parsed = phonenumbers.parse(numero, "BR")
        return is_valid_number(parsed)
    except NumberParseException:
        return False

@app.route("/verifica-emergencia", methods=["POST"])
def verifica_emergencia():
    resposta = request.form.get("SpeechResult", "").lower()
    tentativa = int(request.args.get("tentativa", 1))
    print(f"[RESPOSTA EMERGENCIA - Tentativa {tentativa}] {resposta}")

    confirmacoes = ["ok", "confirma", "entendido", "entendi", "obrigado", "valeu"]

    if any(palavra in resposta for palavra in confirmacoes):
        print("Confirmação recebida do chefe.")
        return _twiml_response("Confirmação recebida. Obrigado.", voice="alice")

    if tentativa < 3:
        print("Sem confirmação. Repetindo mensagem...")
        resp = VoiceResponse()
        gather = Gather(
            input="speech",
            timeout=5,
            speechTimeout="auto",
            action=f"{base_url}/verifica-emergencia?tentativa={tentativa + 1}",
            method="POST",
            language="pt-BR"
        )
        gather.say("Alerta de verificação de segurança. Por favor, confirme dizendo OK ou Entendido.", language="pt-BR", voice="alice")
        resp.append(gather)
        resp.redirect(f"{base_url}/verifica-emergencia?tentativa={tentativa + 1}", method="POST")
        return Response(str(resp), mimetype="text/xml")

    print("Nenhuma confirmação após múltiplas tentativas.")
    return _twiml_response("Nenhuma confirmação recebida. Encerrando a chamada.", voice="alice")

@app.route("/testar-email-emergencia")
def testar_email_emergencia():
    nome_falhou = "Gustavo"
    resposta = "falha na verificacao"

    contatos = load_contacts()
    email_emergencia = contatos.get("email_emergencia")

    if email_emergencia:
        enviar_email_emergencia(
            email_destino=email_emergencia,
            nome=nome_falhou,
            respostas_obtidas=resposta
        )
        return "E-mail de emergência enviado com sucesso!"
    else:
        return "Erro: E-mail de emergência não encontrado ou inválido."

@app.route("/testar-verificacao/<nome>")
def testar_verificacao(nome):
    ligar_para_verificacao_por_nome(nome)
    return f"Ligação de verificação para {nome} iniciada."

def ligar_para_verificacao(numero_destino):
    full_url = f"{base_url}/verifica-sinal?tentativa=1"
    print(f"[LIGANDO] Iniciando ligação para verificação no número: {numero_destino}")
    
    response = VoiceResponse()
    gather = Gather(
        input="speech",
        timeout=5,
        speechTimeout="auto",
        action=full_url,
        method="POST",
        language="pt-BR"
    )
    gather.say("Central de monitoramento?", language="pt-BR", voice="alice")
    response.append(gather)
    response.redirect(full_url, method="POST")

    call = client.calls.create(
        to=numero_destino,
        from_=twilio_number,
        twiml=response
    )
    print(f"[LIGAÇÃO INICIADA] SID da chamada: {call.sid}")

def ligar_para_verificacao_por_nome(nome):
    contatos = load_contacts()
    numero = contatos.get(nome.lower())
    if numero and validar_numero(numero):
        print(f"[AGENDAMENTO] Ligando para {nome}: {numero}")
        ligar_para_verificacao(numero)
    else:
        print(f"[ERRO] Contato '{nome}' não encontrado ou inválido.")

def _twiml_response(texto, voice="alice"):
    resp = VoiceResponse()
    resp.say(texto, language="pt-BR", voice=voice)
    return Response(str(resp), mimetype="text/xml")

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
#       {"nome": "fk", "hora": 9, "minuto": 22},
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
   "jordan": [(11, 50), (11, 55)],
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

#if __name__ == "__main__":
#    app.run(host="0.0.0.0", port=port, debug=True)

#created by Jordanlvs 💼, all rights reserved ®
