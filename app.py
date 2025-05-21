from flask import Flask, request, Response
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather
import json
import os
from datetime import datetime, time
import threading

app = Flask(__name__)

# Configurações Twilio (use suas variáveis de ambiente)
account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_PHONE_NUMBER")

client = Client(account_sid, auth_token)

base_url = os.getenv("BASE_URL", "https://confirmation-u5hq.onrender.com")

def load_contacts():
    with open("contacts.json", "r", encoding="utf-8") as f:
        return json.load(f)

def validar_numero(numero):
    # Validação simples para formato +55DDDNNNNNNNN
    return numero and numero.startswith("+") and len(numero) >= 12

def ligar_para_numero(nome, numero):
    print(f"[LIGANDO] Ligando para {nome} no número {numero}")
    call = client.calls.create(
        to=numero,
        from_=twilio_number,
        twiml=f'''
        <Response>
            <Gather input="speech" timeout="5" speechTimeout="auto" action="{base_url}/verifica-sinal?nome={nome}" method="POST" language="pt-BR">
                <Say voice="Polly.Camila" language="pt-BR">Olá {nome}, por favor, fale a senha de confirmação.</Say>
            </Gather>
            <Say voice="Polly.Camila" language="pt-BR">Não recebi sua resposta. Adeus.</Say>
        </Response>
        '''
    )
    print(f"[LIGANDO] Call SID: {call.sid}")

def ligar_para_emergencia(numero_destino, origem_falha_numero=None, origem_falha_nome=None):
    if origem_falha_nome:
        mensagem = f"{origem_falha_nome} não respondeu à verificação de segurança. Por favor, entre em contato."
    elif origem_falha_numero:
        mensagem = f"O número {origem_falha_numero} não respondeu à verificação de segurança. Por favor, entre em contato."
    else:
        mensagem = "Alguém não respondeu à verificação de segurança. Por favor, entre em contato."

    print(f"[EMERGÊNCIA] Ligando para emergência: {numero_destino} com mensagem: {mensagem}")

    try:
        call = client.calls.create(
            to=numero_destino,
            from_=twilio_number,
            twiml=f'''
            <Response>
                <Say voice="Polly.Camila" language="pt-BR">{mensagem}</Say>
                <Say voice="Polly.Camila" language="pt-BR">Encerrando ligação.</Say>
            </Response>
            '''
        )
        print(f"[EMERGÊNCIA] Ligação de emergência criada. Call SID: {call.sid}")
    except Exception as e:
        print(f"[ERRO] Exceção ao criar chamada de emergência: {e}")

def _twiml_response(text, voice="Polly.Camila"):
    resp = VoiceResponse()
    resp.say(text, voice=voice, language="pt-BR")
    return Response(str(resp), mimetype="text/xml")

@app.route("/testar-verificacao/<nome>", methods=["GET"])
def testar_verificacao(nome):
    contatos = load_contacts()
    numero = contatos.get(nome)
    if not numero or not validar_numero(numero):
        return f"Contato {nome} não encontrado ou número inválido.", 400
    ligar_para_numero(nome, numero)
    return f"Ligação iniciada para {nome} no número {numero}."

@app.route("/verifica-sinal", methods=["POST"])
def verifica_sinal():
    resposta = request.form.get("SpeechResult", "").lower()
    tentativa = int(request.args.get("tentativa", 1))
    nome = request.args.get("nome", "desconhecido")

    print(f"[RESPOSTA - Tentativa {tentativa}] Nome: {nome} Resposta: {resposta}")

    if "protegido" in resposta:
        print("[VERIFICAÇÃO] Resposta correta recebida.")
        return _twiml_response("Entendido. Obrigado.", voice="Polly.Camila")
    elif tentativa < 2:
        resp = VoiceResponse()
        gather = Gather(
            input="speech",
            timeout=5,
            speechTimeout="auto",
            action=f"{base_url}/verifica-sinal?tentativa={tentativa + 1}&nome={nome}",
            method="POST",
            language="pt-BR"
        )
        gather.say("Contra senha incorreta. Fale novamente.", language="pt-BR", voice="Polly.Camila")
        resp.append(gather)
        resp.redirect(f"{base_url}/verifica-sinal?tentativa={tentativa + 1}&nome={nome}", method="POST")
        return Response(str(resp), mimetype="text/xml")
    else:
        contatos = load_contacts()
        numero_emergencia = contatos.get("emergencia")
        contatos_inverso = {v: k for k, v in contatos.items()}
        numero_falhou = request.values.get("To", None)
        nome_falhou = contatos_inverso.get(numero_falhou, nome)

        print(f"[VERIFICAÇÃO] Tentativas esgotadas para {nome_falhou} ({numero_falhou})")

        if numero_emergencia and validar_numero(numero_emergencia):
            try:
                ligar_para_emergencia(numero_emergencia, numero_falhou, nome_falhou)
                print("[VERIFICAÇÃO] Ligação para emergência disparada.")
            except Exception as e:
                print(f"[ERRO] Falha ao ligar para emergência: {e}")
            return _twiml_response("Falha na confirmação. Chamando responsáveis.", voice="Polly.Camila")
        else:
            print("[ERRO] Número de emergência inválido ou não encontrado.")
            return _twiml_response("Erro ao tentar contatar emergência.", voice="Polly.Camila")

# Agendamento das ligações
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()

def agendar_multiplas_ligacoes():
    contatos = load_contacts()

    agendamentos = [
        {"nome": "gustavo", "hora_inicio": 11, "hora_fim": 18, "minuto": 0},
        {"nome": "joão do posto 2", "horarios": [(11,41), (11,45), (11,49)]}  # ligações múltiplas em minutos diferentes
    ]

    for ag in agendamentos:
        nome = ag["nome"]
        numero = contatos.get(nome)
        if not numero or not validar_numero(numero):
            print(f"[AGENDAMENTO] Número inválido ou não encontrado para {nome}. Ignorando agendamento.")
            continue

        if "hora_inicio" in ag and "hora_fim" in ag and "minuto" in ag:
            # Agendamento múltiplo para gustavo de hora_inicio até hora_fim no minuto fixo
            for hora in range(ag["hora_inicio"], ag["hora_fim"] + 1):
                job_id = f"verificacao_{nome.replace(' ', '_')}_{hora:02d}_{ag['minuto']:02d}"
                scheduler.add_job(
                    ligar_para_numero,
                    trigger="cron",
                    hour=hora,
                    minute=ag["minuto"],
                    args=[nome, numero],
                    id=job_id,
                    replace_existing=True
                )
                print(f"[AGENDADO] {job_id} para {nome} às {hora:02d}:{ag['minuto']:02d}")
        elif "horarios" in ag:
            # Agendamento múltiplo de horários específicos (lista de tuplas)
            for hora, minuto in ag["horarios"]:
                job_id = f"verificacao_{nome.replace(' ', '_')}_{hora:02d}_{minuto:02d}"
                scheduler.add_job(
                    ligar_para_numero,
                    trigger="cron",
                    hour=hora,
                    minute=minuto,
                    args=[nome, numero],
                    id=job_id,
                    replace_existing=True
                )
                print(f"[AGENDADO] {job_id} para {nome} às {hora:02d}:{minuto:02d}")

scheduler.start()
agendar_multiplas_ligacoes()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
