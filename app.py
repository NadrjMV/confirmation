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
from pytz import timezone
from collections import deque

load_dotenv()
app = Flask(__name__)

twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_NUMBER")
base_url = os.getenv("BASE_URL")
client = Client(twilio_sid, twilio_token)

CONTACTS_FILE = "contacts.json"
historico_chamadas = deque(maxlen=50)

def load_contacts():
    with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_contacts(data):
    with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _nome_por_numero(numero):
    contatos = load_contacts()
    for nome, tel in contatos.items():
        if tel == numero:
            return nome
    return "desconhecido"

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

@app.route("/painel-dashboard.html")
def painel_dashboard():
    return send_from_directory(".", "painel-dashboard.html")

@app.route("/dashboard")
def dashboard():
    agendadas = scheduler.get_jobs()
    return jsonify({
        "historico": list(historico_chamadas),
        "agendadas": [
            {
                "id": job.id,
                "nome_funcao": job.name,
                "proxima_execucao": job.next_run_time.strftime("%d/%m/%Y %H:%M:%S") if job.next_run_time else "N/A"
            }
            for job in agendadas
        ]
    })

@app.route("/verifica-sinal", methods=["GET", "POST"])
def verifica_sinal():
    resposta = request.form.get("SpeechResult", "").lower()
    tentativa = int(request.args.get("tentativa", 1))
    print(f"[RESPOSTA - Tentativa {tentativa}] {resposta}")

    if "protegido" in resposta:
        print("[SUCESSO] Palavra correta detectada.")
        numero = request.values.get("To")
        historico_chamadas.append({
            "tipo": "verificacao",
            "numero": numero,
            "nome": _nome_por_numero(numero),
            "resultado": "sucesso",
            "horario": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        })
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
        # Usando SSML para evitar corte da fala
        gather.ssml("""
            <speak>
                Contra senha incorreta. Fale novamente.
                <break time="1s"/>
            </speak>
        """)
        resp.append(gather)
        resp.redirect(f"{base_url}/verifica-sinal?tentativa={tentativa + 1}", method="POST")
        return Response(str(resp), mimetype="text/xml")

    print("[FALHA TOTAL] Chamando número de emergência...")
    contatos = load_contacts()
    numero_emergencia = contatos.get("emergencia")

    numero_falhou = request.values.get("To", None)
    nome_falhou = _nome_por_numero(numero_falhou)

    historico_chamadas.append({
        "tipo": "verificacao",
        "numero": numero_falhou,
        "nome": nome_falhou,
        "resultado": "falha",
        "horario": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    })

    if numero_emergencia and validar_numero(numero_emergencia):
        ligar_para_emergencia(numero_emergencia, numero_falhou, nome_falhou)
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
    gather.ssml("""
        <speak>
            Central de monitoramento?
            <break time="1s"/>
        </speak>
    """)
    response.append(gather)
    response.redirect(full_url, method="POST")

    client.calls.create(
        to=numero_destino,
        from_=twilio_number,
        twiml=response
    )

    historico_chamadas.append({
        "tipo": "verificacao",
        "numero": numero_destino,
        "nome": _nome_por_numero(numero_destino),
        "resultado": "iniciada",
        "horario": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    })

def validar_numero(numero):
    try:
        parsed = phonenumbers.parse(numero, "BR")
        return is_valid_number(parsed)
    except NumberParseException:
        return False

def ligar_para_emergencia(numero_destino, origem_falha_numero=None, origem_falha_nome=None):
    if origem_falha_nome:
        mensagem = f"Alerta de verificação de segurança. {origem_falha_nome} não respondeu à verificação de segurança. Por favor, confirme dizendo OK ou Entendido."
    elif origem_falha_numero:
        mensagem = f"O número {origem_falha_numero} não respondeu à verificação de segurança. Por favor, confirme dizendo OK ou Entendido."
    else:
        mensagem = "Alguém não respondeu à verificação de segurança. Por favor, confirme dizendo OK ou Entendido."

    full_url = f"{base_url}/verifica-emergencia?tentativa=1"
    response = VoiceResponse()
    gather = Gather(
        input="speech",
        timeout=5,
        speechTimeout="auto",
        action=full_url,
        method="POST",
        language="pt-BR"
    )
    gather.ssml(f"""
        <speak>
            {mensagem}
            <break time="1s"/>
        </speak>
    """)
    response.append(gather)
    response.redirect(full_url, method="POST")

    client.calls.create(
        to=numero_destino,
        from_=twilio_number,
        twiml=response
    )

    historico_chamadas.append({
        "tipo": "emergencia",
        "numero": numero_destino,
        "nome": _nome_por_numero(numero_destino),
        "resultado": "chamada enviada",
        "horario": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    })

@app.route("/verifica-emergencia", methods=["POST"])
def verifica_emergencia():
    resposta = request.form.get("SpeechResult", "").lower()
    tentativa = int(request.args.get("tentativa", 1))
    print(f"[RESPOSTA EMERGENCIA - Tentativa {tentativa}] {resposta}")

    numero = request.values.get("To")
    sucesso = any(p in resposta for p in ["ok", "confirma", "entendido", "entendi", "obrigado", "valeu"])

    historico_chamadas.append({
        "tipo": "emergencia",
        "numero": numero,
        "nome": _nome_por_numero(numero),
        "resultado": "confirmada" if sucesso else "sem resposta",
        "horario": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    })

    if sucesso:
        return _twiml_response("Confirmação recebida. Obrigado.", voice="Polly.Camila")

    if tentativa < 3:
        resp = VoiceResponse()
        gather = Gather(
            input="speech",
            timeout=5,
            speechTimeout="auto",
            action=f"{base_url}/verifica-emergencia?tentativa={tentativa + 1}",
            method="POST",
            language="pt-BR"
        )
        gather.ssml("""
            <speak>
                Alerta de verificação de segurança. Por favor, confirme dizendo OK ou Entendido.
                <break time="1s"/>
            </speak>
        """)
        resp.append(gather)
        resp.redirect(f"{base_url}/verifica-emergencia?tentativa={tentativa + 1}", method="POST")
        return Response(str(resp), mimetype="text/xml")

    return _twiml_response("Nenhuma confirmação recebida. Encerrando a chamada.", voice="Polly.Camila")

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
#        {"nome": "verificacao1", "hora": datetime.now().hour, "minuto": (datetime.now().minute + 1) % 60},
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
#        {"nome": "fk", "hora": 9, "minuto": 22},
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
   "jordan": [(10, 00), (11, 00), (12, 00)],
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

#created by Jordanlvs 💼, all rights reserved ® 
