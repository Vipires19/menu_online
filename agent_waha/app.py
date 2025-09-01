from flask import Flask, request, jsonify
from services.waha import Waha
from services.agent_graph_imovel import AgentMobi
from services.steve_bot import AgentMike_Graph
from services.bot2 import AgentCmdr
from services.agent_restaurante import AgentRestaurante, atualizar_status_pedido
import time
import random
from langchain_core.prompts.chat import AIMessage,HumanMessage
from langchain_core.messages import ToolMessage
from services.memory import get_memory, create_db_schema
from langgraph.checkpoint.sqlite import SqliteSaver
import logging
import datetime
import ssl
import os
import urllib.parse
from dotenv import load_dotenv,find_dotenv
from pymongo import MongoClient

load_dotenv(find_dotenv())

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
MONGO_USER = urllib.parse.quote_plus(os.getenv('MONGO_USER'))
MONGO_PASS = urllib.parse.quote_plus(os.getenv('MONGO_PASS'))
client = MongoClient("mongodb+srv://%s:%s@cluster0.gjkin5a.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0" % (MONGO_USER, MONGO_PASS))
db = client.restaurante_db
coll3 = db.pedidos

def formatar_mensagem_whatsapp(texto: str) -> str:
    """
    Ajusta a formatação para o padrão do WhatsApp.
    - Transforma **negrito** (markdown) em *negrito* (WhatsApp)
    - Remove excesso de espaços ou caracteres inválidos, se quiser expandir
    """
    return texto.replace("**", "*")

app = Flask(__name__)

#agent_4 = AgentRastreamento(DB_PATH4)
agent_5 = AgentMobi()
agent_6 = AgentMike_Graph()
agent_1 = AgentCmdr()
agent_4 = AgentRestaurante()



#model_4 = agent_4.memory_agent()
model_5 = agent_5.memory_agent()
model_6 = agent_6.memory_agent()
model_1 = agent_1.memory_agent()
model_4 = agent_4.memory_agent()


def agent_memory(agent_model, input: str, thread_id: str, date: str = None):
    try:
        if not thread_id:
            raise ValueError("thread_id é obrigatório no config.")

        # 1) Prepara as entradas e o config
        inputs = {"messages": [{"role": "user", "content": input}]}
        config = {"configurable": {"thread_id": thread_id}}

        print(f"Entradas para o modelo: {inputs}")
        print(">>> [DEBUG] config que será passado para invoke:", config)

        # 2) Executa o grafo
        result = agent_model.invoke(inputs, config)
        print(f"Resultado bruto do grafo: {result}")

        # 3) Extrai a lista interna
        raw = result.get("messages") if isinstance(result, dict) else result

        # 4) Converte cada mensagem em dict simples
        msgs = []
        for m in raw:
            if isinstance(m, (HumanMessage, AIMessage, ToolMessage)):
                msgs.append({"role": m.type, "content": m.content})
            elif isinstance(m, dict):
                msgs.append(m)
            else:
                msgs.append({"role": getattr(m, "role", "assistant"), "content": str(m)})

        # 5) Retorna o conteúdo da última mensagem útil
        ultima = msgs[-1] if msgs else {"content": "⚠️ Nenhuma resposta gerada."}
        return ultima["content"]

    except Exception as e:
        logging.error(f"Erro ao invocar o agente: {str(e)}")
        raise

@app.route('/chatbot/webhook/imobiliaria/', methods=['POST'])
def webhook_5():
    return process_message(model_5, "AGENT5", 'imobiliaria')

@app.route('/chatbot/webhook/policial/', methods=['POST'])
def webhook_6():
    return process_message(model_6, "AGENT6", 'policial')

@app.route('/chatbot/webhook/comodoro/', methods=['POST'])
def webhook_1():
    return process_message(model_1, "AGENT1", 'cmdr')

@app.route('/chatbot/webhook/restaurante/', methods=['POST'])
def webhook_4():
    return process_message(model_4, "AGENT4", 'restaurante')

@app.route('/webhook', methods=['POST'])
def asaas_webhook():
    data = request.json
    print("Webhook do Asaas recebido:", data)

    # Só processa pagamento confirmado
    if data.get("event") != "PAYMENT_RECEIVED":
        return jsonify({"status": "ignored"}), 200

    description = data["payment"].get("description", "")

    # Exemplo: "Pedido #d815e354 - Vinícius - (11)91234-5678 - Pirão Burger"
    import re
    padrao = r"Pedido\s+#(\w+)\s*-\s*(.*?)\s*-\s*(.*?)\s*-"
    match = re.search(padrao, description)

    if not match:
        print("⚠️ Formato inesperado de description:", description)
        return jsonify({"status": "error", "message": "Formato inválido de description"}), 400

    id_pedido = match.group(1).strip()
    nome_cliente = match.group(2).strip()
    telefone = match.group(3).strip()

    # Aqui você pode normalizar o telefone para padrão internacional (ex: 55DDDNUMERO)
    telefone_formatado = telefone.replace("(", "").replace(")", "").replace("-", "").replace(" ", "")
    if not telefone_formatado.startswith("55"):
        telefone_formatado = "55" + telefone_formatado  # adiciona DDI Brasil

    # Mensagem personalizada
    mensagem = (
        f"*Pagamento confirmado!* 🎉\n\n"
        f"✅ Pedido *#{id_pedido}*\n"
        f"👤 Cliente: *{nome_cliente}*\n"
        f"📞 Telefone: {telefone}\n\n"
        f"Obrigado por comprar no Pirão Burger, Seu pedido já foi encaminhado para cozinha 🍔🔥"
    )

    try:
        atualizar_status_pedido(id_pedido, "Enviado para cozinha")
        waha = Waha()
        session = "restaurante"  # ajuste conforme sua sessão do Waha
        chat_id = telefone_formatado + "@c.us"

        waha.start_typing(chat_id=chat_id, session=session)
        time.sleep(random.randint(2, 5))
        waha.send_message(chat_id, mensagem, session)
        waha.stop_typing(chat_id=chat_id, session=session)

        print(f"Mensagem enviada para {chat_id}: {mensagem}")

    except Exception as e:
        print("❌ Erro ao enviar mensagem no WhatsApp:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "success"}), 200
 
def process_message(agent, agent_name, session):#, memory):
    data = request.json
    print(f'EVENTO RECEBIDO ({agent_name}): {data}')

    hoje = datetime.date.today().isoformat()  # Obtenha a data aqui

    try:
        chat_id = data['payload']['from']
        received_message = data['payload']['body']
        
    except KeyError as e:
        print(f"Erro ao acessar dados do payload: {e}")
        return jsonify({'status': 'error', 'message': f"Erro ao acessar dados do payload: {e}"}), 400

    # Evitar spam de eventos irrelevantes
    is_group = '@g.us' in chat_id
    is_status = 'status@broadcast' in chat_id
    msg_type = data['payload'].get('_data', {}).get('type')
    msg_subtype = data['payload'].get('_data', {}).get('subtype')

    if is_group or is_status or msg_type != 'chat' or msg_subtype == 'encrypt' or not received_message:
        return jsonify({'status': 'ignored'}), 200

    try:
        resposta = agent_memory(agent_model=agent, input=received_message, thread_id=chat_id, date=hoje)
        print(f"Resposta gerada: {resposta}")
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

    waha = Waha()
    waha.start_typing(chat_id=chat_id, session=session)
    resposta_format = formatar_mensagem_whatsapp(resposta)
    time.sleep(random.randint(3, 10))
    waha.send_message(chat_id, resposta_format, session)
    waha.stop_typing(chat_id=chat_id, session=session)

    return jsonify({'status': 'success'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
