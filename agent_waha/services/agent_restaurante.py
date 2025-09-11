import pandas as pd
import os
import uuid
import re
import requests
from datetime import datetime, timedelta
from pymongo import MongoClient
from dateutil.parser import parse
import urllib.parse
from langchain_openai import ChatOpenAI
from langchain.tools import tool
from langgraph.prebuilt.tool_node import ToolNode
from langchain_community.document_loaders import Docx2txtLoader
from langgraph.checkpoint.mongodb import MongoDBSaver
from langchain_openai import OpenAIEmbeddings
from langchain_mongodb.vectorstores import MongoDBAtlasVectorSearch
from langchain.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from typing_extensions import TypedDict
from services.waha import Waha
from langgraph.graph import StateGraph, START, END
from langchain_core.runnables import RunnableConfig 
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import Annotated,Dict, Any
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from repositories.wbk_assas import Webhook
from rapidfuzz import process,fuzz
import unicodedata, re, logging
from typing import List, Dict
from bson import ObjectId
import json

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
MONGO_USER = urllib.parse.quote_plus(os.getenv('MONGO_USER'))
MONGO_PASS = urllib.parse.quote_plus(os.getenv('MONGO_PASS'))
MAPS_API_KEY = os.getenv('MAPS_API_KEY')
embedding_model = OpenAIEmbeddings(api_key=OPENAI_API_KEY, model="text-embedding-3-large")
client = MongoClient("mongodb+srv://%s:%s@cluster0.gjkin5a.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0" % (MONGO_USER, MONGO_PASS))
db = client.restaurante_db
coll_memoria = db.memoria_chat
coll_users = db.user
coll3 = db.pedidos
coll4 = db.promocoes
coll5 = db.produtos
coll_vector = db.vetores
coll_entregas = db.entregas
webhook_assas = Webhook()
access_token = os.getenv('ASSAS_ACCESS_TOKEN')
webhook_assas.create_webhook('restaurante', access_token)
waha = Waha()

def carrega_txt(caminho):
    loader = Docx2txtLoader(caminho)
    lista_documentos = loader.load()
    documento = '\n\n'.join([doc.page_content for doc in lista_documentos])
    return documento

memory = MongoDBSaver(coll_memoria)

class State(TypedDict):
    messages: Annotated[list, add_messages]
    user_info: Dict[str, Any]
    pedido: Dict[str, Any]
    tipo_entrega: str  # "retirada" ou "entrega"
    endereco_entrega: Dict[str, Any]  # dados do endereço para entrega
    forma_pagamento: str  # "cartao", "pix", "dinheiro"
    valor_troco: float  # valor necessário para troco
    status_pedido: str  # controle do status do pedido

def check_user(state: dict, config: dict) -> dict:
    """
    Verifica se o usuário já fez contato com o restaurante com base no telefone.
    Adiciona os dados como 'user_info' no estado do LangGraph.
    """
    try:
        thread_id = config["metadata"]["thread_id"]
        sem_sufixo = thread_id.replace("@c.us", "")
        telefone = sem_sufixo[2:]  # remove o 55

        usuario = coll_users.find_one({"telefone": telefone})

        if not usuario:
            # Usuário não existe, mas NÃO criamos automaticamente
            # Vamos perguntar o nome primeiro
            user_info = {
                "nome": None,  # Será preenchido quando o usuário se identificar
                "telefone": telefone,
                "data_criacao": datetime.now().isoformat(),
                "ultima_interacao": datetime.now().isoformat(),
                "status": "aguardando_nome"  # Status especial para usuários não identificados
            }
            
            # NÃO salva no MongoDB ainda - só quando tiver o nome
            print(f"[CHECK_USER] Usuário novo detectado: {telefone} - aguardando identificação")
        else:
            # Usuário existe, atualiza última interação
            data_criacao = usuario.get("data_criacao")
            if hasattr(data_criacao, 'isoformat'):
                data_criacao = data_criacao.isoformat()
            elif data_criacao is None:
                data_criacao = datetime.now().isoformat()
            
            user_info = {
                "nome": usuario.get("nome", None),  # Pode ser None se não foi informado
                "telefone": telefone,
                "data_criacao": data_criacao,
                "ultima_interacao": datetime.now().isoformat(),
                "status": "ativo"
            }
            
            # Atualiza última interação no MongoDB
            try:
                coll_users.update_one(
                    {"telefone": telefone},
                    {"$set": {"ultima_interacao": datetime.now().isoformat()}}
                )
                print(f"[CHECK_USER] Usuário existente atualizado: {telefone}")
            except Exception as e:
                print(f"[CHECK_USER] Erro ao atualizar usuário: {e}")

        # Adiciona user_info ao state
        state["user_info"] = user_info
        
        print(f"[CHECK_USER] User info adicionado ao state: {user_info}")
        return state

    except Exception as e:
        print(f"[CHECK_USER] Erro: {e}")
        # Fallback em caso de erro
        state["user_info"] = {
            "nome": "Erro", 
            "telefone": "erro",
            "data_criacao": datetime.now().isoformat(),
            "ultima_interacao": datetime.now().isoformat(),
            "status": "erro"
        }
        return state

SYSTEM_PROMPT = """
🍔 ATENDENTE VIRTUAL DO PIRÃO BURGER 🍔

Você é o PirãoBot, atendente digital especializado do Pirão Burger! 🌟 Seu objetivo é conduzir o cliente através de um fluxo completo de atendimento de forma profissional e DIVERTIDA! 😄

📋 FLUXO DE ATENDIMENTO OBRIGATÓRIO

1️⃣ SAUDAÇÃO → Cumprimentar calorosamente 😊
2️⃣ IDENTIFICAÇÃO → Se o cliente JÁ tem nome (não é "usuário" ou "None"), NÃO peça o nome! Vá direto para o pedido. Se não tem nome, use criar_usuario! 😊
3️⃣ ANOTAÇÃO DO PEDIDO →

Sempre que o cliente informar um item de pedido, IMEDIATAMENTE usar processar_pedido_full para registrar no sistema 🍔

Depois confirme em texto: “Ótima escolha! 🍔 Seu [item] foi anotado. Deseja adicionar mais alguma coisa?”
4️⃣ CONFIRMAÇÃO DO PEDIDO → Quando o cliente disser "não" ou confirmar o pedido, OBRIGATÓRIO usar a ferramenta confirmar_pedido 💰
5️⃣ TIPO DE ENTREGA → Perguntar se é para RETIRADA ou ENTREGA 🚗
6️⃣ CÁLCULO DE ENTREGA → Se for entrega, usar calcular_entrega para taxa e tempo 📍
7️⃣ FORMA DE PAGAMENTO → Perguntar se será Cartão, PIX ou Dinheiro 💳

Para dinheiro, perguntar quanto o cliente vai pagar para calcular o troco.
8️⃣ FINALIZAÇÃO → Confirmar pedido e informar tempo de preparo 🎉

⚠️ REGRAS CRÍTICAS

✅ NÃO peça o nome se o cliente JÁ tem nome (não é "usuário" ou "None")

✅ Use criar_usuario APENAS quando o cliente não tem nome

✅ Se o cliente tem nome, cumprimente pelo nome e vá direto para o pedido

✅ Sempre use processar_pedido_full assim que o cliente pedir qualquer item.

✅ Nunca avance para entrega ou pagamento sem registrar pelo menos um pedido.

✅ Sempre use confirmar_pedido para confirmação (não apenas texto).

✅ Sempre pergunte sobre retirada ou entrega.

✅ Sempre calcule entrega quando necessário.

✅ Sempre ofereça as 3 formas de pagamento: Cartão, PIX ou Dinheiro.

✅ Para pagamento em dinheiro, sempre calcule o troco.

✅ Nunca use valores fictícios ("XX,XX"). Sempre use valores reais.

✅ Se o cliente falar algo fora do fluxo (ex: "qual horário de funcionamento?"), responda, mas depois volte para o fluxo.

🛠️ FERRAMENTAS DISPONÍVEIS

criar_usuario → Criar novo usuário no banco quando não estiver identificado (nome = None)

atualizar_nome_usuario → Salvar nome do cliente (usar apenas se necessário)

processar_pedido_full → Registrar e validar itens do pedido (sempre que o cliente pedir algo)

confirmar_pedido → Confirmar o pedido final

calcular_entrega → Calcular taxa e tempo de entrega

criar_cobranca_asaas → Gerar links de pagamento (cartão/PIX)

consultar_material_de_apoio → Buscar informações oficiais sobre produtos (nome, preço, descrição)

💬 ESTILO DE COMUNICAÇÃO

Sempre amigável, profissional e divertido 🌟

Use emojis para deixar a conversa leve 🎉

Sempre confirme informações importantes com clareza

Nunca seja seco ou formal demais

Seja simpático, eficiente e divertido 😄

📝 EXEMPLO DE FLUXO CORRETO

👤 Cliente: “Quero um Smash Burger”
🤖 Bot: [usa processar_pedido_full com Smash Burger]
🤖 Bot: “Ótima escolha! 🍔 Seu Smash Burger (R$ 25,00) foi anotado. Deseja adicionar mais alguma coisa?”

👤 Cliente: “Não”
🤖 Bot: [usa confirmar_pedido]
🤖 Bot: “Perfeito! Agora me diga, é para RETIRADA ou ENTREGA? 🚗”

👤 Cliente: “Entrega”
🤖 Bot: “Beleza! Me informe seu endereço para calcular a taxa de entrega 📍”

(...continua o fluxo com pagamento e finalização...)
"""

def buscar_produtos_cardapio():
    """
    Busca todos os produtos disponíveis no MongoDB e retorna um JSON com:
    - produto (nome)
    - valor do produto
    - adicionais disponíveis
    """
    try:
        # Busca apenas produtos disponíveis
        produtos_cursor = coll5.find(
            {"disponivel": True},
            {
                "nome": 1,
                "preco": 1,
                "valor": 1,  # alguns produtos podem usar 'valor' ao invés de 'preco'
                "categoria": 1,
                "adicionais": 1,
                "_id": 0  # exclui o _id do resultado
            }
        )
        
        produtos_lista = []
        
        for produto in produtos_cursor:
            # Trata tanto 'preco' quanto 'valor' como preço
            valor_produto = produto.get('preco') or produto.get('valor', 0.0)
            
            # Processa adicionais - pode ser array de strings ou objetos
            adicionais_processados = []
            adicionais_raw = produto.get('adicionais', [])
            
            for adicional in adicionais_raw:
                if isinstance(adicional, dict):
                    # Se for objeto, pega nome e valor
                    adicionais_processados.append({
                        "nome": adicional.get("nome", ""),
                        "valor": adicional.get("preco", adicional.get("valor", 0.0))
                    })
                else:
                    # Se for string simples
                    adicionais_processados.append({
                        "nome": str(adicional),
                        "valor": 0.0  # valor padrão para adicionais sem preço
                    })
            
            produto_formatado = {
                "produto": produto.get("nome", ""),
                "categoria": produto.get("categoria", ""),
                "valor": float(valor_produto),
                "adicionais": adicionais_processados
            }
            
            produtos_lista.append(produto_formatado)
        
        # Ordena por categoria e depois por nome
        produtos_lista.sort(key=lambda x: (x["categoria"], x["produto"]))
        
        resultado = {
            "success": True,
            "total_produtos": len(produtos_lista),
            "produtos": produtos_lista
        }
        
        return json.dumps(resultado, ensure_ascii=False, indent=2)
        
    except Exception as e:
        erro = {
            "success": False,
            "erro": f"Erro ao buscar produtos: {str(e)}"
        }
        return json.dumps(erro, ensure_ascii=False, indent=2)

def buscar_produtos_por_categoria(categoria):
    """
    Busca produtos de uma categoria específica
    """
    try:
        produtos_cursor = coll5.find(
            {
                "disponivel": True,
                "categoria": {"$regex": f"^{categoria}$", "$options": "i"}
            },
            {
                "nome": 1,
                "preco": 1,
                "valor": 1,
                "categoria": 1,
                "adicionais": 1,
                "_id": 0
            }
        )
        
        produtos_lista = []
        
        for produto in produtos_cursor:
            valor_produto = produto.get('preco') or produto.get('valor', 0.0)
            
            adicionais_processados = []
            adicionais_raw = produto.get('adicionais', [])
            
            for adicional in adicionais_raw:
                if isinstance(adicional, dict):
                    adicionais_processados.append({
                        "nome": adicional.get("nome", ""),
                        "valor": adicional.get("preco", adicional.get("valor", 0.0))
                    })
                else:
                    adicionais_processados.append({
                        "nome": str(adicional),
                        "valor": 0.0
                    })
            
            produto_formatado = {
                "produto": produto.get("nome", ""),
                "categoria": produto.get("categoria", ""),
                "valor": float(valor_produto),
                "adicionais": adicionais_processados
            }
            
            produtos_lista.append(produto_formatado)
        
        resultado = {
            "success": True,
            "categoria": categoria,
            "total_produtos": len(produtos_lista),
            "produtos": produtos_lista
        }
        
        return json.dumps(resultado, ensure_ascii=False, indent=2)
        
    except Exception as e:
        erro = {
            "success": False,
            "erro": f"Erro ao buscar produtos da categoria {categoria}: {str(e)}"
        }
        return json.dumps(erro, ensure_ascii=False, indent=2)
    
@tool("consultar_material_de_apoio")
def consultar_material_de_apoio(pergunta: str) -> str:
    """
    Consulta o material de apoio técnico enviado pelos personal trainers para responder perguntas específicas.
    """
    vectorStore = MongoDBAtlasVectorSearch(coll_vector, embedding=embedding_model, index_name='default')
    docs = vectorStore.similarity_search(pergunta)
    if not docs:
        return "Nenhum conteúdo relevante encontrado no material de apoio."
    
    return "\n\n".join([doc.page_content[:400] for doc in docs])

def normalizar(texto: str) -> str:
    texto = texto.lower()
    texto = "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    return texto.strip()

def atualizar_status_pedido(pedido_id: str, novo_status: str, descricao: str = None, dados_extras: dict = None):
    """
    Atualiza o status do pedido no MongoDB e mantém histórico.
    """
    try:
        # Base do update
        update_data = {
            "$set": {
                "status": novo_status,
                "data_atualizacao": datetime.utcnow().isoformat(),
            },
            "$push": {
                "historico_status": {
                    "status": novo_status,
                    "data": datetime.utcnow().isoformat(),
                    "descricao": descricao or f"Status alterado para: {novo_status}"
                }
            }
        }

        # Adiciona dados extras em $set
        if dados_extras:
            for key, value in dados_extras.items():
                if key != "historico_status":
                    update_data["$set"][key] = value

        # Atualiza no banco
        result = coll3.update_one({"id_pedido": pedido_id}, update_data)

        if result.modified_count > 0:
            print(f"[STATUS] Pedido {pedido_id} atualizado para: {novo_status}")
            return True
        else:
            print(f"[STATUS] Nenhuma modificação no pedido {pedido_id}")
            return False

    except Exception as e:
        print(f"[ERRO] Erro ao atualizar status do pedido {pedido_id}: {str(e)}")
        return False

@tool("criar_usuario")
def criar_usuario(nome_cliente: str, state: dict) -> str:
    """
    Cria um novo usuário no banco de dados quando ele não estiver identificado.
    Pega o nome do usuário e salva no MongoDB com o telefone do state.
    """
    try:
        print(f"[CRIAR_USUARIO] Iniciando criação de usuário: {nome_cliente}")
        
        # Valida se o nome foi fornecido
        if not nome_cliente or nome_cliente.strip() == "":
            return "❌ Erro: Nome do cliente não pode estar vazio. Por favor, informe seu nome."
        
        # Pega o telefone do state
        telefone = None
        if state and "user_info" in state:
            telefone = state["user_info"].get("telefone")
            print(f"[CRIAR_USUARIO] Telefone obtido do state: {telefone}")
        
        # Se não tem telefone no state, não pode criar usuário
        if not telefone:
            return "❌ Erro: Não foi possível identificar o telefone do cliente. Tente novamente."
        
        # Verifica se já existe um usuário com este telefone
        usuario_existente = coll_users.find_one({"telefone": telefone})
        
        if usuario_existente:
            # Se o usuário já existe, apenas atualiza o nome se estiver vazio
            if not usuario_existente.get("nome") or usuario_existente.get("nome") == "Não informado":
                result = coll_users.update_one(
                    {"telefone": telefone},
                    {
                        "$set": {
                            "nome": nome_cliente.strip(),
                            "ultima_interacao": datetime.now().isoformat(),
                            "status": "ativo"
                        }
                    }
                )
                
                if result.modified_count > 0:
                    print(f"[CRIAR_USUARIO] Nome atualizado para usuário existente: {nome_cliente}")
                    
                    # Atualiza também no state
                    if state and "user_info" in state:
                        state["user_info"]["nome"] = nome_cliente.strip()
                        state["user_info"]["status"] = "ativo"
                        print(f"[CRIAR_USUARIO] State atualizado: {nome_cliente}")
                    
                    return f"✅ Nome atualizado com sucesso! Olá, {nome_cliente}! 😊"
                else:
                    return f"⚠️ Nome já estava atualizado: {nome_cliente}"
            else:
                # Usuário já tem nome, não sobrescreve
                nome_atual = usuario_existente.get("nome")
                return f"ℹ️ Usuário já identificado como: {nome_atual}. Se quiser alterar, use a ferramenta atualizar_nome_usuario."
        else:
            # Cria novo usuário no MongoDB
            novo_usuario = {
                "nome": nome_cliente.strip(),
                "telefone": telefone,
                "data_criacao": datetime.now().isoformat(),
                "ultima_interacao": datetime.now().isoformat(),
                "status": "ativo"
            }
            
            result = coll_users.insert_one(novo_usuario)
            
            if result.inserted_id:
                print(f"[CRIAR_USUARIO] Novo usuário criado: {nome_cliente} - {telefone}")
                
                # Atualiza o state com os dados do novo usuário
                if state and "user_info" in state:
                    state["user_info"]["nome"] = nome_cliente.strip()
                    state["user_info"]["data_criacao"] = novo_usuario["data_criacao"]
                    state["user_info"]["status"] = "ativo"
                    print(f"[CRIAR_USUARIO] State atualizado: {nome_cliente}")
                
                return f"✅ Usuário criado com sucesso! Olá, {nome_cliente}! 😊\n\nAgora posso te ajudar com seu pedido! 🍔"
            else:
                return "❌ Erro ao criar usuário no banco de dados. Tente novamente."
                
    except Exception as e:
        print(f"[CRIAR_USUARIO] Erro: {e}")
        return f"❌ Erro ao criar usuário: {str(e)}"

@tool("processar_pedido_full")
def processar_pedido_full(text: str,
                          nome_cliente: str = None,
                          telefone: str = None,
                          auto_accept_threshold: int = 80,
                          fuzzy_prod_threshold: int = 80,
                          state: dict = None) -> dict:
    """
    Tool para processar pedidos complexos com múltiplos itens, adicionais específicos e observações.
    NOVA FUNCIONALIDADE: Acumula itens em um único pedido em vez de criar pedidos separados.
    
    Exemplo de entrada: "dois pirão burger sem cebola com bacon extra em um deles e quero tambem mais um smash burger"
    
    Retorna estrutura detalhada para cozinha, caixa e entregador:
    - success: bool
    - need_confirmation: bool (se True, confirmação necessária)
    - confirmations: list (detalhes p/ UI)
    - order: resumo do pedido (id, valor_total, itens detalhados)
    - message: texto humano para enviar ao cliente
    """
    
    # IMPORTANTE: Sempre tenta pegar o telefone do state primeiro (mais confiável)
    if not telefone and state and "user_info" in state:
        telefone = state["user_info"].get("telefone")
        print(f"[PEDIDO] Telefone obtido do state: {telefone}")
    
    # Se não recebeu nome_cliente, tenta pegar do state
    if not nome_cliente:
        if state and "user_info" in state:
            user_info = state["user_info"]
            nome_cliente = user_info.get("nome", "Cliente")
            print(f"[PEDIDO] Nome obtido do state: {nome_cliente}")
        else:
            nome_cliente = "Cliente"
            print(f"[PEDIDO] Usando nome padrão: {nome_cliente}")
    
    # Garante que temos um telefone válido
    if not telefone:
        print(f"[PEDIDO] ERRO: Telefone não encontrado!")
        return {
            "success": False,
            "message": "❌ Erro: Não foi possível identificar o telefone do cliente. Tente novamente."
        }
    
    print(f"[PEDIDO] Dados finais: nome={nome_cliente}, telefone={telefone}")
    
    # NOVA LÓGICA: Verifica se já existe um pedido em andamento para este cliente
    pedido_existente = None
    if state and "pedido" in state:
        pedido_existente = state["pedido"]
        print(f"[PEDIDO] Pedido existente encontrado no state: {pedido_existente.get('id_pedido')}")
    else:
        # Busca pedido existente no banco pelo telefone do cliente
        pedido_db = coll3.find_one(
            {
                "cliente.telefone": telefone,
                "status": {"$in": ["Aguardando definição de entrega", "Aguardando forma de pagamento"]}
            },
            sort=[("data_criacao", -1)]
        )
        if pedido_db:
            pedido_existente = pedido_db
            print(f"[PEDIDO] Pedido existente encontrado no banco: {pedido_db.get('id_pedido')}")
            # Atualiza o state com o pedido encontrado
            if state:
                state["pedido"] = pedido_db
    
    # ---------- CONSTANTES E HELPERS ----------
    NUM_WORDS = {
        "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "três": 3, "quatro": 4,
        "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
        "onze": 11, "doze": 12, "treze": 13, "quatorze": 14, "quinze": 15
    }
    
    # Expressões que indicam especificação parcial
    ESPECIFICACOES_PARCIAIS = [
        r'\b(?:em\s+um\s+deles?|só\s+um\s+deles?|apenas\s+um|um\s+só|no\s+primeiro)\b',
        r'\b(?:em\s+uma?\s+delas?|só\s+uma?\s+delas?|apenas\s+uma?|uma?\s+só)\b',
        r'\b(?:no\s+primeiro|no\s+segundo|no\s+terceiro|no\s+quarto|no\s+quinto)\b',
        r'\b(?:primeiro\s+com|segundo\s+com|terceiro\s+com|quarto\s+com|quinto\s+com)\b'
    ]
    
    def normalizar(s: str) -> str:
        """Normaliza texto para comparação fuzzy"""
        if not s:
            return ""
        s = s.lower()
        s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
        s = re.sub(r'[^\w\s]', '', s)
        return s.strip()

    def _achar_produto_fuzzy(nome_busca: str, min_score: int = 80):
        """Busca produto usando fuzzy matching com threshold mais restritivo"""
        if not nome_busca:
            return None, None
        produtos_cursor = list(coll5.find({"disponivel": True}))
        if not produtos_cursor:
            return None, None
        nomes = [normalizar(p.get("nome", "")) for p in produtos_cursor]
        match = process.extractOne(normalizar(nome_busca), nomes, scorer=fuzz.ratio)
        if not match:
            return None, None
        matched_norm, score, idx = match
        produto = produtos_cursor[idx]
        if score < min_score:
            return None, int(score)
        return produto, int(score)

    def _match_adicional_fuzzy(nome_adicional: str, adicionais_db: list, limit=3):
        """Busca adicional usando fuzzy matching"""
        if not nome_adicional:
            return None, 0, []
        nomes_db = []
        for ad in adicionais_db:
            if isinstance(ad, dict):
                nomes_db.append(ad.get("nome", ""))
            else:
                nomes_db.append(str(ad))
        nomes_norm = [normalizar(n) for n in nomes_db]
        query = normalizar(nome_adicional)
        if not any(nomes_norm):
            return None, 0, []
        match = process.extractOne(query, nomes_norm, scorer=fuzz.ratio)
        if not match:
            return None, 0, []
        matched_norm, score, idx = match
        best_name = nomes_db[idx]
        sug_raw = process.extract(query, nomes_norm, scorer=fuzz.ratio, limit=limit)
        suggestions = [nomes_db[i] for _, _, i in sug_raw]
        return best_name, int(score), suggestions

    def _buscar_produtos_similares(nome_busca: str, limit=5):
        """Busca produtos similares para sugestão quando produto não é encontrado"""
        if not nome_busca:
            return []
        
        produtos_cursor = list(coll5.find({"disponivel": True}))
        if not produtos_cursor:
            return []
        
        # Cria lista de produtos com nomes normalizados
        produtos_com_scores = []
        query_norm = normalizar(nome_busca)
        
        for produto in produtos_cursor:
            nome_produto = produto.get("nome", "")
            nome_norm = normalizar(nome_produto)
            score = fuzz.ratio(query_norm, nome_norm)
            
            # Só inclui produtos com score mínimo de 40
            if score >= 40:
                produtos_com_scores.append({
                    "nome": nome_produto,
                    "score": score,
                    "produto": produto
                })
        
        # Ordena por score (maior primeiro) e retorna os melhores
        produtos_com_scores.sort(key=lambda x: x["score"], reverse=True)
        return produtos_com_scores[:limit]

    def _parse_items_from_text(text_in: str):
        """
        Parsing inteligente para pedidos complexos.
        
        Exemplo: "dois pirão burger sem cebola com bacon extra em um deles e quero tambem mais um smash burger"
        
        Resultado esperado:
        - Item 1: Pirão burger + bacon extra + sem cebola
        - Item 2: Pirão burger + sem cebola (sem bacon)
        - Item 3: Smash burger (sem adicionais, sem observações)
        """
        if not text_in or not text_in.strip():
            return []
        
        text = text_in.lower().strip()
        print(f"[DEBUG] Texto original: {text}")
        
        # Busca produtos disponíveis para matching
        produtos_cursor = list(coll5.find({"disponivel": True}, {"nome": 1}))
        product_names = [p["nome"] for p in produtos_cursor] if produtos_cursor else []
        
        # Divide o texto em segmentos principais (por vírgula, "e", ponto e vírgula)
        # Usa regex mais inteligente para separar itens
        segments = re.split(r'\s*(?:,|;|\s+e\s+(?=\d|\w+\s+\w+))\s*', text)
        print(f"[DEBUG] Segmentos separados: {segments}")
        
        parsed_items = []
        last_items_for_product: list[dict] = []  # itens do último produto detectado (para anexar 'com/sem')
        
        for segment_idx, segment in enumerate(segments):
            segment = segment.strip()
            if not segment:
                continue
                
            print(f"[DEBUG] Processando segmento {segment_idx + 1}: '{segment}'")

            # Se o segmento é apenas modificadores (começa com 'sem ' ou 'com '), anexa ao último produto
            if re.match(r'^sem\s+', segment):
                if last_items_for_product:
                    obs_local = re.findall(r'\bsem\s+([^,;]+?)(?=\s*(?:,|;|$|\bcom\b))', segment)
                    observacoes = [f"sem {o.strip()}" for o in obs_local]
                    for it in last_items_for_product:
                        obs_exist = it.get("observacoes", "")
                        merge = "; ".join([v for v in [obs_exist] + observacoes if v])
                        it["observacoes"] = merge
                    print(f"[DEBUG] Anexadas observações ao último produto: {observacoes}")
                    continue
                # Se não há produto anterior, segue o fluxo normal para evitar perder info

            if re.match(r'^com\s+', segment):
                if last_items_for_product:
                    adicionais_globais = []
                    com_matches_local = re.findall(r'\bcom\s+([^,;]+?)(?=\s*(?:,|;|$|\bsem\b))', segment)
                    for adicional_text in com_matches_local:
                        adicionais_split = re.split(r'\s+e\s+', adicional_text)
                        for ad in adicionais_split:
                            ad = ad.strip()
                            if ad:
                                adicionais_globais.append(ad)
                    tem_especificacao_parcial = bool(re.search(r'\b(?:em\s+um\s+deles?|só\s+um\s+deles?|apenas\s+um|um\s+só|no\s+primeiro)\b', segment))
                    for i, it in enumerate(last_items_for_product):
                        if tem_especificacao_parcial and i > 0:
                            continue
                        it.setdefault("adicionais", [])
                        it["adicionais"].extend(adicionais_globais)
                    print(f"[DEBUG] Anexados adicionais ao último produto: {adicionais_globais}")
                    continue
                # Se não há produto anterior, segue o fluxo normal
            
            # Extrai quantidade (numérica ou por extenso)
            quantidade = 1
            qtd_match = re.search(r'(\d+)\s*(?:x|vezes)?\b', segment)
            if qtd_match:
                quantidade = int(qtd_match.group(1))
                segment = re.sub(r'\d+\s*(?:x|vezes)?\b', '', segment).strip()
                print(f"[DEBUG] Quantidade numérica encontrada: {quantidade}")
            else:
                # Busca quantidade por extenso no início
                for word, num in NUM_WORDS.items():
                    if segment.startswith(word + ' '):
                        quantidade = num
                        segment = segment[len(word):].strip()
                        print(f"[DEBUG] Quantidade por extenso: {num} ({word})")
                        break
            
            # Se o segmento ficou vazio após extrair quantidade, pula
            if not segment.strip():
                print(f"[DEBUG] Segmento vazio após extrair quantidade, pulando")
                continue
            
            # Identifica o produto principal usando fuzzy matching
            produto_principal = None
            best_score = 0
            
            if product_names:
                # Tenta match com o segmento completo primeiro
                match = process.extractOne(segment, product_names, scorer=fuzz.ratio)
                if match and match[1] >= fuzzy_prod_threshold:
                    produto_principal = match[0]
                    best_score = match[1]
                    print(f"[DEBUG] Match direto com produto: {produto_principal} (score: {best_score})")
                else:
                    # Tenta com combinações de palavras
                    words = re.findall(r'[\wçãâéíóúõ]+', segment)
                    for n in range(min(4, len(words)), 0, -1):
                        for i in range(len(words) - n + 1):
                            candidate = " ".join(words[i:i+n])
                            match = process.extractOne(candidate, product_names, scorer=fuzz.ratio)
                            if match and match[1] >= fuzzy_prod_threshold and match[1] > best_score:
                                produto_principal = match[0]
                                best_score = match[1]
                                print(f"[DEBUG] Match com combinação '{candidate}': {produto_principal} (score: {best_score})")
            
            if not produto_principal:
                # Se não encontrou produto e o segmento é muito curto ou vazio, pula
                if len(segment.strip()) < 3:
                    print(f"[DEBUG] Segmento muito curto, pulando: '{segment}'")
                    continue
                produto_principal = segment  # fallback para o texto original
                print(f"[DEBUG] Usando fallback: {produto_principal}")
            
            # Extrai observações globais (tudo que vem depois de "sem")
            observacoes_globais = []
            sem_matches = re.findall(r'\bsem\s+([^,;]+?)(?=\s*(?:,|;|$|\bcom\b))', segment)
            for obs in sem_matches:
                observacoes_globais.append(f"sem {obs.strip()}")
            print(f"[DEBUG] Observações globais: {observacoes_globais}")
            
            # Extrai adicionais globais (tudo que vem depois de "com")
            adicionais_globais = []
            com_matches = re.findall(r'\bcom\s+([^,;]+?)(?=\s*(?:,|;|$|\bsem\b))', segment)
            for adicional_text in com_matches:
                # Divide adicionais por "e"
                adicionais_split = re.split(r'\s+e\s+', adicional_text)
                for ad in adicionais_split:
                    ad = ad.strip()
                    if ad:
                        adicionais_globais.append(ad)
            print(f"[DEBUG] Adicionais globais: {adicionais_globais}")
            
            # Verifica se há especificação parcial ("em um deles", "só um deles", etc.)
            tem_especificacao_parcial = False
            for pattern in ESPECIFICACOES_PARCIAIS:
                if re.search(pattern, segment):
                    tem_especificacao_parcial = True
                    print(f"[DEBUG] Especificação parcial encontrada com padrão: {pattern}")
                    break
            
            # Gera os itens individuais
            for i in range(quantidade):
                item_observacoes = list(observacoes_globais)  # Observações sempre aplicam a todos
                
                # Adicionais: se tem especificação parcial, só aplica no primeiro item
                if tem_especificacao_parcial:
                    item_adicionais = list(adicionais_globais) if i == 0 else []
                    print(f"[DEBUG] Item {i+1}: {'COM' if i == 0 else 'SEM'} adicionais (especificação parcial)")
                else:
                    item_adicionais = list(adicionais_globais)  # Aplica a todos
                    print(f"[DEBUG] Item {i+1}: COM adicionais (aplicação global)")
                
                item_cur = {
                    "nome_produto": produto_principal,
                    "adicionais": item_adicionais,
                    "observacoes": " ; ".join(item_observacoes) if item_observacoes else "",
                    "quantidade": 1,  # Cada item individual tem quantidade 1
                    "especificacao_parcial": tem_especificacao_parcial and i == 0  # Marca se é o item com especificação
                }
                parsed_items.append(item_cur)
                # atualiza o buffer do último produto
                if i == 0:
                    last_items_for_product = []
                last_items_for_product.append(item_cur)
        
        print(f"[DEBUG] Total de itens parseados: {len(parsed_items)}")
        return parsed_items

    # ---------- INÍCIO DO FLUXO DA TOOL ----------
    try:
        print(f"[v1] Processando pedido: {text}")
        
        # 1) Parse inteligente do texto
        parsed = _parse_items_from_text(text)
        print(f"[v1] Itens parseados: {len(parsed)} itens")
        for i, item in enumerate(parsed):
            print(f"[v1] Item {i+1}: {item['nome_produto']} | Adicionais: {item['adicionais']} | Obs: {item['observacoes']}")
        
        if not parsed:
            return {"success": False, "message": "Não consegui identificar itens no pedido. Pode reescrever?"}

        # 2) Validação dos itens
        itens_validados = []
        confirmations = []
        all_ok = True
        
        for idx, item in enumerate(parsed):
            nome_produto = item.get("nome_produto", "").strip()
            
            # Busca o produto no banco
            produto_doc, score = _achar_produto_fuzzy(nome_produto)
            
            if not produto_doc:
                # Busca produtos similares para sugerir
                produtos_similares = _buscar_produtos_similares(nome_produto)
                if produtos_similares:
                    sugestoes = [p["nome"] for p in produtos_similares[:3]]
                    return {
                        "success": False, 
                        "need_confirmation": True,
                        "message": f"❌ Produto '{nome_produto}' não encontrado no cardápio.\n\n🤔 Você quis dizer um destes?\n" + 
                                 "\n".join([f"• {sug}" for sug in sugestoes]) + 
                                 f"\n\nPor favor, confirme qual produto você deseja! 😊"
                    }
                else:
                    return {
                        "success": False, 
                        "message": f"❌ Produto '{nome_produto}' não encontrado no cardápio. Pode verificar o nome?"
                    }
            
            print(f"[v1] Produto encontrado: {produto_doc.get('nome')} (score: {score})")
            
            # Se o score for baixo (menos de 80), pede confirmação
            if score < 80:
                # Busca produtos similares para mostrar alternativas
                produtos_similares = _buscar_produtos_similares(nome_produto)
                sugestoes = [p["nome"] for p in produtos_similares[:3] if p["nome"] != produto_doc.get("nome")]
                
                mensagem_confirmacao = f"🤔 Você pediu '{nome_produto}' mas encontrei '{produto_doc.get('nome')}' (similaridade: {score}%)\n\n"
                
                if sugestoes:
                    mensagem_confirmacao += f"Outras opções disponíveis:\n" + "\n".join([f"• {sug}" for sug in sugestoes]) + "\n\n"
                
                mensagem_confirmacao += f"✅ Confirma que quer o '{produto_doc.get('nome')}' ou prefere outro?"
                
                return {
                    "success": False,
                    "need_confirmation": True,
                    "message": mensagem_confirmacao,
                    "produto_sugerido": {
                        "nome_original": nome_produto,
                        "nome_sugerido": produto_doc.get('nome'),
                        "score": score,
                        "alternativas": sugestoes
                    }
                }
            
            # Valida adicionais
            adicionais_db = produto_doc.get("adicionais", [])
            adicionais_validados = []
            adicionais_invalidos = []
            
            for adicional in item.get("adicionais", []):
                best_name, best_score, suggestions = _match_adicional_fuzzy(adicional, adicionais_db)
                
                if best_name and best_score >= auto_accept_threshold:
                    # Busca preço do adicional
                    preco_adicional = 0.0
                    for ad_db in adicionais_db:
                        nome_ad_db = ad_db.get("nome") if isinstance(ad_db, dict) else str(ad_db)
                        if normalizar(nome_ad_db) == normalizar(best_name):
                            preco_adicional = float(ad_db.get("preco", ad_db.get("valor", 0)))
                            break
                    
                    adicionais_validados.append({
                        "nome": best_name,
                        "valor": preco_adicional
                    })
                    print(f"[v1] Adicional aceito: {best_name} (R$ {preco_adicional})")
                else:
                    adicionais_invalidos.append({
                        "original": adicional,
                        "suggestions": suggestions[:3]  # Máximo 3 sugestões
                    })
                    all_ok = False
                    print(f"[v1] Adicional inválido: {adicional} (sugestões: {suggestions[:3]})")
            
            item_validado = {
                "nome_produto": produto_doc.get("nome"),
                "produto_id": str(produto_doc.get("_id")),
                "adicionais": adicionais_validados,
                "observacoes": item.get("observacoes", ""),
                "quantidade": 1,
                "especificacao_parcial": item.get("especificacao_parcial", False)
            }
            itens_validados.append(item_validado)
            
            if adicionais_invalidos:
                confirmations.append({
                    "idx": idx,
                    "produto": produto_doc.get("nome"),
                    "adicionais_invalidos": adicionais_invalidos
                })
        
        # Se há adicionais inválidos, retorna para confirmação
        if not all_ok:
            return {
                "success": False,
                "need_confirmation": True,
                "confirmations": confirmations,
                "message": "Alguns adicionais precisam de confirmação. Verifique as sugestões."
            }
        
        # 3) Monta os novos itens para adicionar ao pedido
        novos_itens = []
        valor_novos_itens = 0.0
        
        for idx, item in enumerate(itens_validados):
            produto_doc = coll5.find_one({"_id": ObjectId(item["produto_id"])})
            preco_base = float(produto_doc.get("preco", 0.0))
            valor_adicionais = sum([ad["valor"] for ad in item["adicionais"]])
            subtotal = round(preco_base + valor_adicionais, 2)
            valor_novos_itens += subtotal
            
            print(f"[PEDIDO] Item {idx+1}: {item['nome_produto']} - Preço base: R$ {preco_base:.2f}, Adicionais: R$ {valor_adicionais:.2f}, Subtotal: R$ {subtotal:.2f}")
            print(f"[PEDIDO] Produto encontrado: {produto_doc.get('nome')} - Preço: R$ {produto_doc.get('preco')}")
            
            # Estrutura detalhada para cozinha, caixa e entregador
            item_detalhado = {
                "produto": item["nome_produto"],
                "produto_id": item["produto_id"],
                "quantidade": 1,
                "valor_unitario": preco_base,
                "adicionais": item["adicionais"],
                "observacoes": item["observacoes"],
                "subtotal": subtotal,
                "especificacao_parcial": item.get("especificacao_parcial", False),
                "instrucoes_cozinha": {
                    "produto": item["nome_produto"],
                    "adicionais": [ad["nome"] for ad in item["adicionais"]],
                    "observacoes": item["observacoes"],
                    "especificacao_parcial": item.get("especificacao_parcial", False)
                }
            }
            
            novos_itens.append(item_detalhado)
        
        print(f"[PEDIDO] Valor dos novos itens: R$ {valor_novos_itens:.2f}")
        
        # 4) LÓGICA DE ACUMULAÇÃO: Adiciona itens ao pedido existente ou cria novo
        if pedido_existente:
            # PEDIDO EXISTENTE: Adiciona novos itens ao pedido atual
            print(f"[PEDIDO] Adicionando {len(novos_itens)} itens ao pedido existente: {pedido_existente.get('id_pedido')}")
            
            # Calcula próximo item_id baseado nos itens existentes
            itens_existentes = pedido_existente.get("itens", [])
            proximo_item_id = len(itens_existentes) + 1
            
            # Atualiza item_id dos novos itens
            for i, item in enumerate(novos_itens):
                item["item_id"] = proximo_item_id + i
            
            # Adiciona novos itens aos existentes
            itens_atualizados = itens_existentes + novos_itens
            valor_total_atualizado = pedido_existente.get("valor_total", 0) + valor_novos_itens
            
            # Atualiza o pedido no banco
            coll3.update_one(
                {"id_pedido": pedido_existente.get("id_pedido")},
                {
                    "$set": {
                        "itens": itens_atualizados,
                        "valor_total": round(valor_total_atualizado, 2),
                        "valor_total_final": round(valor_total_atualizado, 2),
                        "data_atualizacao": datetime.utcnow().isoformat()
                    },
                    "$push": {
                        "historico_status": {
                            "status": "Itens adicionados",
                            "data": datetime.utcnow().isoformat(),
                            "descricao": f"Adicionados {len(novos_itens)} novos itens ao pedido"
                        }
                    }
                }
            )
            
            # Atualiza o pedido no state
            pedido_existente["itens"] = itens_atualizados
            pedido_existente["valor_total"] = round(valor_total_atualizado, 2)
            pedido_existente["valor_total_final"] = round(valor_total_atualizado, 2)
            
            if state:
                state["pedido"] = pedido_existente
                state["status_pedido"] = "itens_adicionados"
            
            pedido = pedido_existente
            itens_pedido = itens_atualizados
            valor_total = valor_total_atualizado
            
        else:
            # NOVO PEDIDO: Cria um pedido completamente novo
            print(f"[PEDIDO] Criando novo pedido com {len(novos_itens)} itens")
            
            id_pedido = str(uuid.uuid4())[:8]
            
            # Atualiza item_id dos novos itens
            for i, item in enumerate(novos_itens):
                item["item_id"] = i + 1
            
            # Cria estrutura completa do pedido
            pedido = {
                "id_pedido": id_pedido,
                "cliente": {"nome": nome_cliente, "telefone": telefone},
                "itens": novos_itens,
                "valor_total": round(valor_novos_itens, 2),
                "status": "Aguardando definição de entrega",
                "data_criacao": datetime.utcnow().isoformat(),
                "data_atualizacao": datetime.utcnow().isoformat(),
                "tipo_entrega": None,  # será preenchido depois
                "endereco_entrega": None,
                "forma_pagamento": None,
                "valor_entrega": 0.0,
                "valor_total_final": round(valor_novos_itens, 2),  # Inicialmente igual ao valor do pedido
                "historico_status": [
                    {
                        "status": "Aguardando definição de entrega",
                        "data": datetime.utcnow().isoformat(),
                        "descricao": "Pedido criado e aguardando definição de entrega/retirada"
                    }
                ],
                "estrutura_detalhada": {
                    "total_itens": len(novos_itens),
                    "itens_por_produto": {},
                    "resumo_cozinha": [],
                    "resumo_caixa": [],
                    "resumo_entregador": []
                }
            }
            
            # Salva no MongoDB
            coll3.insert_one(pedido)
            print(f"[PEDIDO] Novo pedido salvo no MongoDB: {id_pedido}")
            
            # Atualiza o state
            if state:
                state["pedido"] = pedido
                state["status_pedido"] = "pedido_anotado"
            
            itens_pedido = novos_itens
            valor_total = valor_novos_itens
        
        # 5) Atualiza estrutura detalhada (só para pedidos novos)
        if not pedido_existente:
            # Preenche estrutura detalhada para pedidos novos
            for item in itens_pedido:
                produto_nome = item["produto"]
                if produto_nome not in pedido["estrutura_detalhada"]["itens_por_produto"]:
                    pedido["estrutura_detalhada"]["itens_por_produto"][produto_nome] = []
                
                pedido["estrutura_detalhada"]["itens_por_produto"][produto_nome].append({
                    "item_id": item["item_id"],
                    "adicionais": item["adicionais"],
                    "observacoes": item["observacoes"],
                    "subtotal": item["subtotal"]
                })
                
                # Resumo para cozinha
                resumo_cozinha = f"Item {item['item_id']}: {item['produto']}"
                if item['adicionais']:
                    resumo_cozinha += f" + {', '.join([ad['nome'] for ad in item['adicionais']])}"
                if item['observacoes']:
                    resumo_cozinha += f" | {item['observacoes']}"
                pedido["estrutura_detalhada"]["resumo_cozinha"].append(resumo_cozinha)
                
                # Resumo para caixa
                resumo_caixa = f"Item {item['item_id']}: {item['produto']} = R$ {item['subtotal']:.2f}"
                pedido["estrutura_detalhada"]["resumo_caixa"].append(resumo_caixa)
                
                # Resumo para entregador
                resumo_entregador = f"Item {item['item_id']}: {item['produto']}"
                if item['observacoes']:
                    resumo_entregador += f" ({item['observacoes']})"
                pedido["estrutura_detalhada"]["resumo_entregador"].append(resumo_entregador)
            
            # Atualiza estrutura detalhada no banco
            coll3.update_one(
                {"id_pedido": pedido["id_pedido"]},
                {"$set": {"estrutura_detalhada": pedido["estrutura_detalhada"]}}
            )
        
        # 6) Monta resposta detalhada para o cliente
        resumo_itens = []
        for item in itens_pedido:
            linha = f"• {item['produto']} (R$ {item['valor_unitario']:.2f})"
            if item['adicionais']:
                adicionais_texto = ", ".join([f"{ad['nome']} (+R$ {ad['valor']:.2f})" for ad in item['adicionais']])
                linha += f" + {adicionais_texto}"
            if item['observacoes']:
                linha += f" | {item['observacoes']}"
            linha += f" = R$ {item['subtotal']:.2f}"
            resumo_itens.append(linha)
        
        # Mensagem diferenciada para pedido existente vs novo
        if pedido_existente:
            mensagem = (
                f"✅ *Itens adicionados ao pedido!*\n\n"
                f"🆔 *ID:* {pedido['id_pedido']}\n"
                f"👤 *Cliente:* {nome_cliente}\n"
                f"📦 *Total de itens:* {len(itens_pedido)}\n\n"
                f"📋 *Itens do pedido:*\n" + "\n".join(resumo_itens) + "\n\n"
                f"💰 *Valor total:* R$ {pedido['valor_total']:.2f}\n\n"
                f"🚗 *Agora preciso saber:* É para **RETIRADA** ou **ENTREGA**?"
            )
        else:
            mensagem = (
                f"✅ *Pedido anotado com sucesso!*\n\n"
                f"🆔 *ID:* {pedido['id_pedido']}\n"
                f"👤 *Cliente:* {nome_cliente}\n"
                f"📦 *Total de itens:* {len(itens_pedido)}\n\n"
                f"📋 *Itens do pedido:*\n" + "\n".join(resumo_itens) + "\n\n"
                f"💰 *Valor total:* R$ {pedido['valor_total']:.2f}\n\n"
                f"🚗 *Agora preciso saber:* É para **RETIRADA** ou **ENTREGA**?"
            )
        
        # 7) Retorna estrutura completa para o app Django
        return {
            "success": True,
            "order": {
                "id_pedido": pedido["id_pedido"],
                "valor_total": pedido["valor_total"],
                "total_itens": len(itens_pedido),
                "itens": itens_pedido,
                "estrutura_detalhada": pedido.get("estrutura_detalhada", {})
            },
            "message": mensagem,
            "estrutura_cozinha": pedido.get("estrutura_detalhada", {}).get("resumo_cozinha", []),
            "estrutura_caixa": pedido.get("estrutura_detalhada", {}).get("resumo_caixa", []),
            "estrutura_entregador": pedido.get("estrutura_detalhada", {}).get("resumo_entregador", [])
        }
        
    except Exception as e:
        print(f"[v1] Erro em processar_pedido_full: {str(e)}")
        return {"success": False, "message": f"Erro interno: {str(e)}"}

@tool("calcular_entrega")
def calcular_entrega(endereco_cliente: str = None, 
                     lat: float = None, 
                     lon: float = None, 
                     state: dict = None) -> dict:
    """
    Calcula a entrega com base no endereço ou localização do cliente.
    Retorna distância, tempo e valor da entrega.
    Atualiza o pedido no banco de dados com as informações de entrega.
    """
    try:
        # Endereço fixo do restaurante
        endereco_restaurante = "Av. Paris, 707, Ribeirão Preto, SP"
        lat_restaurante = -21.163050737652213
        lon_restaurante = -47.784856112034205

        # Monta origem e destino
        if lat is not None and lon is not None:
            origem = f"{lat_restaurante},{lon_restaurante}"
            destino = f"{lat},{lon}"
            endereco_final = f"Coordenadas: {lat}, {lon}"
        elif endereco_cliente:
            origem = endereco_restaurante
            destino = endereco_cliente
            endereco_final = endereco_cliente
        else:
            return {
                "success": False,
                "message": "❌ Preciso do seu endereço ou localização para calcular a entrega!"
            }

        # Chamada à API Distance Matrix
        url = (
            f"https://maps.googleapis.com/maps/api/distancematrix/json?"
            f"origins={urllib.parse.quote(origem)}&destinations={urllib.parse.quote(destino)}"
            f"&key={MAPS_API_KEY}&units=metric&language=pt-BR"
        )
        
        response = requests.get(url)
        try:
            res = response.json()
        except Exception:
            res = {"status": "ERROR", "rows": []}

        if not isinstance(res, dict) or res.get("status") != "OK":
            return {
                "success": False,
                "message": "❌ Erro na API do Google Maps. Tente novamente."
            }

        try:
            elemento = res["rows"][0]["elements"][0]
        except Exception:
            return {
                "success": False,
                "message": "❌ Resposta inesperada da API de mapas. Tente novamente."
            }

        if elemento.get("status") != "OK":
            return {
                "success": False,
                "message": "❌ Não foi possível calcular a entrega. Verifique o endereço informado."
            }

        distancia_km = elemento["distance"]["value"] / 1000  # metros para km
        tempo_estimado = elemento["duration"]["text"]
        
        # Taxa base + valor por km
        taxa_base = 3.00  # taxa mínima
        valor_por_km = 1.50  # valor por km
        valor_entrega = round(taxa_base + (distancia_km * valor_por_km), 2)
        
        # Valor mínimo e máximo
        valor_entrega = max(valor_entrega, 3.00)  # mínimo R$ 3,00
        valor_entrega = min(valor_entrega, 15.00)  # máximo R$ 15,00

        pedido_id = None
        valor_base = 0.0

        if state and "pedido" in state:
            pedido_id = state["pedido"].get("id_pedido")
            valor_base = float(state["pedido"].get("valor_total", 0.0))

        # Se não tiver pedido no state, tenta buscar no banco pelo último
        if not pedido_id:
            pedido_db = coll3.find_one({}, sort=[("data_criacao", -1)])
            if pedido_db:
                pedido_id = pedido_db.get("id_pedido")
                valor_base = float(pedido_db.get("valor_total", 0.0))

        if not pedido_id:
            return {
                "success": False,
                "message": "❌ Nenhum pedido encontrado para calcular entrega."
            }

        valor_total_final = valor_base + valor_entrega

        # Dados de entrega para salvar
        dados_entrega = {
            "tipo_entrega": "entrega",
            "endereco_entrega": {
                "endereco": endereco_final,
                "distancia_km": distancia_km,
                "tempo_estimado": tempo_estimado
            },
            "valor_entrega": valor_entrega,
            "valor_total_final": valor_total_final
        }

        # Atualiza no banco de dados
        coll3.update_one(
            {"id_pedido": pedido_id},
            {"$set": dados_entrega}
        )

        # Atualiza também status
        atualizar_status_pedido(
            pedido_id=pedido_id,
            novo_status="Aguardando forma de pagamento",
            descricao=f"Entrega calculada: {distancia_km:.1f}km, taxa R$ {valor_entrega:.2f}",
            dados_extras=dados_entrega
        )

        # Atualiza o state local
        if state:
            state["tipo_entrega"] = "entrega"
            state["endereco_entrega"] = {
                "endereco": endereco_final,
                "distancia_km": distancia_km,
                "tempo_estimado": tempo_estimado,
                "valor_entrega": valor_entrega
            }
            state["status_pedido"] = "entrega_calculada"
            if "pedido" in state:
                state["pedido"]["valor_total_final"] = valor_total_final

        mensagem = (
            f"🚚 *Entrega calculada!*\n\n"
            f"📍 *Endereço:* {endereco_final}\n"
            f"📏 *Distância:* {distancia_km:.1f} km\n"
            f"⏱️ *Tempo estimado:* {tempo_estimado}\n"
            f"💰 *Taxa de entrega:* R$ {valor_entrega:.2f}\n\n"
            f"*Valor total do pedido:* R$ {valor_total_final:.2f}\n\n"
            f"💳 *Como deseja pagar?*\n"
            f"1️⃣ Cartão de Crédito/Débito\n"
            f"2️⃣ PIX\n"
            f"3️⃣ Dinheiro na entrega"
        )

        return {
            "success": True,
            "distancia_km": distancia_km,
            "tempo_estimado": tempo_estimado,
            "valor_entrega": valor_entrega,
            "endereco": endereco_final,
            "message": mensagem
        }

    except Exception as e:
        print(f"[v0] Erro ao calcular entrega: {str(e)}")
        return {
            "success": False,
            "message": f"❌ Erro ao calcular entrega: {str(e)}"
        }

@tool("processar_retirada")
def processar_retirada(state: dict = None) -> dict:
    """
    Processa a opção de retirada no balcão.
    Atualiza o pedido e solicita forma de pagamento.
    """
    try:
        # Busca o pedido no estado ou no banco de dados
        pedido = None
        pedido_id = None
        valor_pedido = 0
        
        if state and "pedido" in state:
            pedido = state["pedido"]
            pedido_id = pedido.get("id_pedido")
            valor_pedido = pedido.get("valor_total", 0)
        else:
            # Se não está no estado, busca no banco pelo telefone do usuário
            user_info = state.get("user_info", {}) if state else {}
            telefone = user_info.get("telefone", "")
            
            print(f"[PROCESSAR_RETIRADA] Buscando pedido para telefone: {telefone}")
            
            if telefone:
                # Busca o pedido mais recente do usuário
                pedido_db = coll3.find_one(
                    {"cliente.telefone": telefone},
                    sort=[("data_criacao", -1)]
                )
                
                if pedido_db:
                    pedido = pedido_db
                    pedido_id = pedido_db.get("id_pedido")
                    valor_pedido = pedido_db.get("valor_total", 0)
                    print(f"[PROCESSAR_RETIRADA] Pedido encontrado: {pedido_id} - R$ {valor_pedido}")
                    # Atualiza o estado com o pedido encontrado
                    if state:
                        state["pedido"] = pedido_db
                else:
                    # Se não encontrou pelo telefone, tenta buscar o último pedido criado
                    print(f"[PROCESSAR_RETIRADA] Pedido não encontrado pelo telefone, buscando último pedido...")
                    pedido_db = coll3.find_one(
                        {},
                        sort=[("data_criacao", -1)]
                    )
                    
                    if pedido_db:
                        pedido = pedido_db
                        pedido_id = pedido_db.get("id_pedido")
                        valor_pedido = pedido_db.get("valor_total", 0)
                        print(f"[PROCESSAR_RETIRADA] Último pedido encontrado: {pedido_id} - R$ {valor_pedido}")
                        # Atualiza o estado com o pedido encontrado
                        if state:
                            state["pedido"] = pedido_db
                    else:
                        print(f"[PROCESSAR_RETIRADA] Nenhum pedido encontrado no sistema")
                        return {
                            "success": False,
                            "message": "❌ Erro: pedido não encontrado no sistema."
                        }
            else:
                print(f"[PROCESSAR_RETIRADA] Telefone não encontrado no state")
                return {
                    "success": False,
                    "message": "❌ Erro: pedido não encontrado no sistema."
                }
        
        # Atualiza status e dados da retirada
        dados_retirada = {
            "tipo_entrega": "retirada",
            "valor_entrega": 0.0,
            "valor_total_final": valor_pedido
        }
        
        atualizar_status_pedido(
            pedido_id=pedido_id,
            novo_status="Aguardando forma de pagamento",
            descricao="Retirada no balcão confirmada",
            dados_extras=dados_retirada
        )
        
        # Atualiza o estado (só se state não for None)
        if state:
            state["tipo_entrega"] = "retirada"
            state["status_pedido"] = "retirada_confirmada"
        
        mensagem = (
            f"🏪 *Retirada no balcão confirmada!*\n\n"
            f"📍 *Local:* Pirão Burger - Av. Paris, 707\n"
            f"⏱️ *Tempo de preparo:* 20-30 minutos\n"
            f"💰 *Valor total:* R$ {valor_pedido:.2f}\n\n"
            f"💳 *Como deseja pagar?*\n"
            f"1️⃣ Cartão de Crédito/Débito (na retirada)\n"
            f"2️⃣ PIX (na retirada)\n"
            f"3️⃣ Dinheiro na retirada"
        )
        
        return {
            "success": True,
            "message": mensagem
        }
        
    except Exception as e:
        print(f"[v0] Erro ao processar retirada: {str(e)}")
        return {
            "success": False,
            "message": f"❌ Erro ao processar retirada: {str(e)}"
        }

@tool("criar_cobranca_asaas")
def criar_cobranca_asaas(
    tipo: str,  # "CREDIT_CARD" ou "PIX"
    customer_id: str = 'cus_000006650523',
    state: dict | None = None) -> str:
    """
    Cria uma cobrança via Asaas para cartão ou PIX.
    Tipos aceitos: CREDIT_CARD, PIX
    """
    try:
        pedido_db = None
        id_pedido = None

        # Primeiro tenta pegar do state
        if state and "pedido" in state:
            id_pedido = state["pedido"].get("id_pedido")
            if id_pedido:
                pedido_db = coll3.find_one({"id_pedido": id_pedido})
        
        # Verifica se o pedido já foi criado
        if not pedido_db:
            print("[COBRANCA] Pedido não encontrado no state, buscando último pedido...")
            pedido_db = coll3.find_one({}, sort=[("data_criacao", -1)])
            
        if not pedido_db:
            print("[COBRANCA] ERRO: Nenhum pedido encontrado no sistema!")
            return "❌ Erro: Nenhum pedido encontrado no sistema. Tente fazer um novo pedido."
        
        # Verifica se o pedido tem dados válidos
        if not pedido_db.get("cliente") or not pedido_db.get("itens"):
            print("[COBRANCA] ERRO: Pedido incompleto!")
            return "❌ Erro: Pedido incompleto. Tente fazer um novo pedido."
        
        print(f"[COBRANCA] Pedido validado: {pedido_db.get('id_pedido')} com {len(pedido_db.get('itens', []))} itens")
        print(f"[COBRANCA] Dados do pedido: cliente={pedido_db.get('cliente')}, valor_total={pedido_db.get('valor_total')}")

        # Dados do pedido
        id_pedido = pedido_db.get("id_pedido")
        valor_total = pedido_db.get("valor_total_final", pedido_db.get("valor_total", 0))
        tipo_entrega = pedido_db.get("tipo_entrega", "")
        
        # Se for retirada, não gera link de pagamento
        if tipo_entrega == "retirada":
            print(f"[COBRANCA] Pedido é para retirada, não gerando link de pagamento")
            
            # Atualiza o status do pedido
            dados_pagamento = {
                "forma_pagamento": tipo.lower(),
                "status_pagamento": "aguardando_pagamento_retirada"
            }
            atualizar_status_pedido(
                pedido_id=id_pedido,
                novo_status="Aguardando pagamento na retirada",
                descricao=f"Pagamento {tipo} será realizado na retirada",
                dados_extras=dados_pagamento
            )
            
            mensagem = (
                f"✅ *Pagamento {tipo} confirmado para retirada!*\n\n"
                f"🧾 *Pedido:* #{id_pedido}\n"
                f"💰 *Valor:* R$ {valor_total:.2f}\n"
                f"💳 *Forma:* {tipo}\n\n"
                f"🏪 *Retirada:* Pirão Burger - Av. Paris, 707\n"
                f"⏱️ *Tempo de preparo:* 20-30 minutos\n\n"
                f"💳 *Pagamento será realizado na retirada!*"
            )
            
            return mensagem

        # Busca dados do usuário com prioridade inteligente
        nome = "Cliente"
        telefone = "indefinido"
        
        # 1. Primeiro tenta do pedido (mais atualizado)
        if pedido_db and "cliente" in pedido_db:
            pedido_cliente = pedido_db.get("cliente", {})
            pedido_nome = pedido_cliente.get("nome")
            pedido_telefone = pedido_cliente.get("telefone")
            
            # Usa nome do pedido se disponível e não for "Não informado"
            if pedido_nome and pedido_nome != "Não informado":
                nome = pedido_nome
            
            # Usa telefone do pedido se disponível
            if pedido_telefone:
                telefone = pedido_telefone
                print(f"[COBRANCA] Telefone encontrado no pedido: {telefone}")
        
        # 2. Se não tem nome válido do pedido, tenta do state
        if nome == "Cliente" and state and "user_info" in state:
            state_user_info = state["user_info"]
            state_nome = state_user_info.get("nome")
            
            # Usa nome do state se disponível e não for "Não informado"
            if state_nome and state_nome != "Não informado":
                nome = state_nome
                print(f"[COBRANCA] Nome encontrado no state: {nome}")
        
        # 3. Se ainda não tem telefone, busca do state
        if telefone == "indefinido" or not telefone:
            if state and "user_info" in state:
                state_user_info = state["user_info"]
                state_telefone = state_user_info.get("telefone")
                
                # Usa telefone do state se disponível
                if state_telefone and state_telefone != "indefinido":
                    telefone = state_telefone
                    print(f"[COBRANCA] Telefone encontrado no state: {telefone}")
        
        # Garante que temos um telefone válido
        if telefone == "indefinido" or not telefone:
            print(f"[COBRANCA] ERRO: Não foi possível encontrar telefone válido!")
            return "❌ Erro: Não foi possível identificar o telefone do cliente. Tente novamente."

        print(f"[COBRANCA] Dados finais do usuário: nome={nome}, telefone={telefone}")
        print(f"[COBRANCA] Dados do pedido: id={id_pedido}, valor={valor_total}")

        vencimento = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        descricao = f"Pedido #{id_pedido} - {nome} - {telefone} - Pirão Burger"

        # Cria a cobrança na API
        url = "https://api-sandbox.asaas.com/v3/payments"
        headers = {
            "Content-Type": "application/json",
            "access_token": access_token
        }

        payload = {
            "customer": customer_id,
            "billingType": tipo,
            "value": valor_total,
            "dueDate": vencimento,
            "description": descricao,
            "externalReference": id_pedido
        }

        response = requests.post(url, json=payload, headers=headers)
        if response.status_code not in [200, 201]:
            return f"❌ Erro ao gerar cobrança: {response.status_code} - {response.text}"

        cobranca = response.json()

        if tipo == "PIX":
            link_pagamento = cobranca.get("invoiceUrl")
            qr_code = cobranca.get("pixQrCode")
            tipo_texto = "PIX"
        else:
            link_pagamento = cobranca.get("invoiceUrl")
            tipo_texto = "Cartão de Crédito/Débito"

        # Atualiza o status do pedido no banco
        dados_pagamento = {
            "forma_pagamento": tipo.lower(),
            "cobranca_id": cobranca.get("id"),
            "link_pagamento": link_pagamento
        }
        atualizar_status_pedido(
            pedido_id=id_pedido,
            novo_status="Aguardando pagamento",
            descricao=f"Cobrança {tipo} gerada - ID: {cobranca.get('id')}",
            dados_extras=dados_pagamento
        )

        # Mensagem final para o usuário
        if tipo == "PIX":
            mensagem = (
                f"✅ *Cobrança PIX gerada!*\n\n"
                f"🧾 *Pedido:* #{id_pedido}\n"
                f"💰 *Valor:* R$ {valor_total:.2f}\n"
                f"📅 *Vencimento:* {vencimento}\n\n"
                f"💳 Pague pelo link abaixo:\n{link_pagamento}\n\n"
                f"📱 *Ou use o código PIX:*\n`{qr_code}`\n\n"
                f"⏱️ *Após o pagamento, seu pedido entrará na fila de preparo!*"
            )
        else:
            mensagem = (
                f"✅ *Link de pagamento gerado!*\n\n"
                f"🧾 *Pedido:* #{id_pedido}\n"
                f"💰 *Valor:* R$ {valor_total:.2f}\n"
                f"💳 *Forma:* {tipo_texto}\n"
                f"📅 *Vencimento:* {vencimento}\n\n"
                f"💳 Pague pelo link abaixo:\n{link_pagamento}\n\n"
                f"⏱️ *Após o pagamento, seu pedido entrará na fila de preparo!*"
            )

        return mensagem

    except Exception as e:
        print(f"[v0] Erro ao criar cobrança: {str(e)}")
        return f"❌ Erro ao gerar cobrança: {str(e)}"

@tool("processar_pagamento_dinheiro")
def processar_pagamento_dinheiro(valor_cliente: float, state: dict = None) -> str:
    """
    Processa pagamento em dinheiro e calcula o troco necessário.
    """
    try:
        # Busca o pedido no estado ou no banco de dados
        pedido = None
        id_pedido = None
        valor_pedido = 0
        valor_entrega = 0
        
        # Primeiro tenta pegar do state
        if state and "pedido" in state:
            pedido = state["pedido"]
            id_pedido = pedido.get("id_pedido")
            valor_pedido = pedido.get("valor_total", 0)
            valor_entrega = state.get("endereco_entrega", {}).get("valor_entrega", 0)
            print(f"[PAGAMENTO_DINHEIRO] Pedido encontrado no state: {id_pedido}")
        else:
            # Se não está no state, busca no banco
            print(f"[PAGAMENTO_DINHEIRO] Pedido não encontrado no state, buscando no banco...")
            
            # Tenta buscar pelo telefone do usuário primeiro
            user_info = state.get("user_info", {}) if state else {}
            telefone = user_info.get("telefone", "")
            
            if telefone:
                print(f"[PAGAMENTO_DINHEIRO] Buscando pedido para telefone: {telefone}")
                # Busca pedidos do usuário que ainda não foram pagos
                pedido_db = coll3.find_one(
                    {
                        "cliente.telefone": telefone,
                        "status": {"$in": ["Aguardando definição de entrega", "Aguardando forma de pagamento", "Confirmado - Preparando"]}
                    },
                    sort=[("data_criacao", -1)]
                )
                
                if pedido_db:
                    pedido = pedido_db
                    id_pedido = pedido_db.get("id_pedido")
                    valor_pedido = pedido_db.get("valor_total", 0)
                    valor_entrega = pedido_db.get("valor_entrega", 0)
                    print(f"[PAGAMENTO_DINHEIRO] Pedido encontrado pelo telefone: {id_pedido} - Status: {pedido_db.get('status')}")
                    # Atualiza o state com o pedido encontrado
                    if state:
                        state["pedido"] = pedido_db
                else:
                    print(f"[PAGAMENTO_DINHEIRO] Pedido não encontrado pelo telefone")
            
            # Se não encontrou pelo telefone, busca o último pedido criado que ainda não foi pago
            if not pedido:
                print(f"[PAGAMENTO_DINHEIRO] Buscando último pedido não pago...")
                pedido_db = coll3.find_one(
                    {
                        "status": {"$in": ["Aguardando definição de entrega", "Aguardando forma de pagamento", "Confirmado - Preparando"]}
                    },
                    sort=[("data_criacao", -1)]
                )
                
                if pedido_db:
                    pedido = pedido_db
                    id_pedido = pedido_db.get("id_pedido")
                    valor_pedido = pedido_db.get("valor_total", 0)
                    valor_entrega = pedido_db.get("valor_entrega", 0)
                    print(f"[PAGAMENTO_DINHEIRO] Último pedido não pago encontrado: {id_pedido} - Status: {pedido_db.get('status')}")
                    # Atualiza o state com o pedido encontrado
                    if state:
                        state["pedido"] = pedido_db
                else:
                    print(f"[PAGAMENTO_DINHEIRO] Nenhum pedido não pago encontrado no sistema")
                    return "❌ Erro: pedido não encontrado no sistema."
        
        # Verifica se encontrou o pedido
        if not pedido or not id_pedido:
            print(f"[PAGAMENTO_DINHEIRO] ERRO: Pedido não encontrado após todas as tentativas")
            return "❌ Erro: pedido não encontrado no sistema."
        
        # Verifica se o pedido encontrado é válido para pagamento
        status_pedido = pedido.get("status", "")
        if status_pedido in ["Enviado para cozinha", "Finalizado", "Cancelado"]:
            print(f"[PAGAMENTO_DINHEIRO] ERRO: Pedido {id_pedido} já foi processado (status: {status_pedido})")
            return "❌ Erro: Este pedido já foi processado anteriormente."
        
        # Calcula valor total
        valor_total = valor_pedido + valor_entrega
        
        # Log detalhado dos valores
        print(f"[PAGAMENTO_DINHEIRO] Valores calculados:")
        print(f"[PAGAMENTO_DINHEIRO] - Valor pedido: R$ {valor_pedido:.2f}")
        print(f"[PAGAMENTO_DINHEIRO] - Valor entrega: R$ {valor_entrega:.2f}")
        print(f"[PAGAMENTO_DINHEIRO] - Valor total: R$ {valor_total:.2f}")
        print(f"[PAGAMENTO_DINHEIRO] - Valor cliente: R$ {valor_cliente:.2f}")
        print(f"[PAGAMENTO_DINHEIRO] - Status do pedido: {status_pedido}")
        print(f"[PAGAMENTO_DINHEIRO] - ID do pedido: {id_pedido}")
        
        # Verifica se o valor é suficiente
        if valor_cliente < valor_total:
            diferenca = valor_total - valor_cliente
            return (
                f"❌ *Valor insuficiente!*\n\n"
                f"💰 *Total do pedido:* R$ {valor_total:.2f}\n"
                f"💵 *Valor informado:* R$ {valor_cliente:.2f}\n"
                f"❗ *Faltam:* R$ {diferenca:.2f}\n\n"
                f"Por favor, informe um valor igual ou maior que R$ {valor_total:.2f}"
            )
        
        # Calcula troco
        troco = valor_cliente - valor_total
        
        # Atualiza status e dados do pagamento
        dados_pagamento = {
            "forma_pagamento": "dinheiro",
            "valor_recebido": valor_cliente,
            "troco": troco,
            "status_pagamento": "confirmado"
        }
        
        print(f"[PAGAMENTO_DINHEIRO] Atualizando pedido {id_pedido} com dados: {dados_pagamento}")
        
        atualizar_status_pedido(
            pedido_id=id_pedido,
            novo_status="Enviado para cozinha",
            descricao=f"Pagamento em dinheiro confirmado - Recebido: R$ {valor_cliente:.2f}, Troco: R$ {troco:.2f}",
            dados_extras=dados_pagamento
        )
        
        # Atualiza o estado (só se state não for None)
        if state:
            state["forma_pagamento"] = "dinheiro"
            state["valor_troco"] = troco
            state["status_pedido"] = "confirmado"
            # Atualiza o pedido no state com os dados de pagamento
            if "pedido" in state:
                state["pedido"]["forma_pagamento"] = "dinheiro"
                state["pedido"]["valor_recebido"] = valor_cliente
                state["pedido"]["troco"] = troco
                state["pedido"]["status"] = "Enviado para cozinha"
        
        # Log final da mensagem
        print(f"[PAGAMENTO_DINHEIRO] Montando mensagem final com valores:")
        print(f"[PAGAMENTO_DINHEIRO] - ID: {id_pedido}")
        print(f"[PAGAMENTO_DINHEIRO] - Total: R$ {valor_total:.2f}")
        print(f"[PAGAMENTO_DINHEIRO] - Valor recebido: R$ {valor_cliente:.2f}")
        print(f"[PAGAMENTO_DINHEIRO] - Troco: R$ {troco:.2f}")
        
        if troco > 0:
            mensagem = (
                f"✅ *Pedido confirmado e enviado para cozinha!*\n\n"
                f"🧾 *Pedido:* #{id_pedido}\n"
                f"💰 *Total:* R$ {valor_total:.2f}\n"
                f"💵 *Valor recebido:* R$ {valor_cliente:.2f}\n"
                f"💸 *Troco:* R$ {troco:.2f}\n\n"
                f"📋 *Status:* Enviado para cozinha\n"
                f"⏱️ *Tempo estimado:* 20-30 minutos\n\n"
                f"🚚 *Tipo:* {(state.get('tipo_entrega', 'retirada') if state else 'retirada').title()}\n"
                f"💳 *Pagamento:* Dinheiro (troco: R$ {troco:.2f})"
            )
        else:
            mensagem = (
                f"✅ *Pedido confirmado e enviado para cozinha!*\n\n"
                f"🧾 *Pedido:* #{id_pedido}\n"
                f"💰 *Total:* R$ {valor_total:.2f}\n"
                f"💵 *Valor exato recebido!*\n\n"
                f"📋 *Status:* Enviado para cozinha\n"
                f"⏱️ *Tempo estimado:* 20-30 minutos\n\n"
                f"🚚 *Tipo:* {(state.get('tipo_entrega', 'retirada') if state else 'retirada').title()}\n"
                f"💳 *Pagamento:* Dinheiro"
            )
        
        print(f"[PAGAMENTO_DINHEIRO] Mensagem final montada com sucesso")
        return mensagem
        
    except Exception as e:
        print(f"[PAGAMENTO_DINHEIRO] Erro ao processar pagamento em dinheiro: {str(e)}")
        return f"❌ Erro ao processar pagamento: {str(e)}"

@tool("atualizar_nome_usuario")
def atualizar_nome_usuario(nome_cliente: str, state: dict = None) -> str:
    """
    Atualiza o nome do usuário no state e no banco de dados quando ele se identifica.
    """
    try:
        print(f"[ATUALIZAR_NOME] Iniciando atualização para: {nome_cliente}")
        
        # Se não tem state, busca o usuário mais recente no banco
        if not state or "user_info" not in state:
            print("[ATUALIZAR_NOME] State não disponível, buscando usuário mais recente...")
            # Busca o usuário mais recente
            usuario_recente = coll_users.find_one(
                {"telefone": {"$exists": True, "$ne": ""}},
                sort=[("ultima_interacao", -1)]
            )
            if not usuario_recente:
                print("[ATUALIZAR_NOME] Nenhum usuário encontrado, criando novo...")
                # Cria um novo usuário com telefone genérico (será atualizado depois)
                novo_usuario = {
                    "nome": nome_cliente,
                    "telefone": "16981394877",  # Telefone padrão para teste
                    "data_criacao": datetime.now().isoformat(),
                    "ultima_interacao": datetime.now().isoformat(),
                    "status": "ativo"
                }
                result = coll_users.insert_one(novo_usuario)
                if result.inserted_id:
                    print(f"[ATUALIZAR_NOME] Novo usuário criado: {nome_cliente}")
                    return f"✅ Usuário criado com sucesso: {nome_cliente}!"
                else:
                    return "❌ Erro ao criar usuário no banco de dados."
            telefone = usuario_recente.get("telefone")
        else:
            telefone = state["user_info"].get("telefone")
            if not telefone:
                return "❌ Erro: Telefone do usuário não encontrado."
        
        print(f"[ATUALIZAR_NOME] Telefone encontrado: {telefone}")
        
        # Verifica se o usuário existe no banco
        usuario_existente = coll_users.find_one({"telefone": telefone})
        
        if usuario_existente:
            # Atualiza usuário existente
            result = coll_users.update_one(
                {"telefone": telefone},
                {
                    "$set": {
                        "nome": nome_cliente,
                        "ultima_interacao": datetime.now().isoformat(),
                        "status": "ativo"
                    }
                }
            )
            
            if result.modified_count > 0:
                print(f"[ATUALIZAR_NOME] Nome atualizado para '{nome_cliente}' no telefone {telefone}")
                
                # Atualiza também no state se disponível
                if state and "user_info" in state:
                    state["user_info"]["nome"] = nome_cliente
                    print(f"[ATUALIZAR_NOME] State atualizado: {nome_cliente}")
                
                return f"✅ Nome atualizado com sucesso para '{nome_cliente}'!"
            else:
                print(f"[ATUALIZAR_NOME] Nenhuma modificação no usuário: {telefone}")
                return f"⚠️ Nome já estava atualizado: {nome_cliente}"
        else:
            # Cria novo usuário
            novo_usuario = {
                "nome": nome_cliente,
                "telefone": telefone,
                "data_criacao": datetime.now().isoformat(),
                "ultima_interacao": datetime.now().isoformat(),
                "status": "ativo"
            }
            
            result = coll_users.insert_one(novo_usuario)
            if result.inserted_id:
                print(f"[ATUALIZAR_NOME] Novo usuário criado: {nome_cliente} - {telefone}")
                
                # Atualiza também no state se disponível
                if state and "user_info" in state:
                    state["user_info"]["nome"] = nome_cliente
                    print(f"[ATUALIZAR_NOME] State atualizado: {nome_cliente}")
                
                return f"✅ Usuário criado com sucesso: {nome_cliente}!"
            else:
                return "❌ Erro ao criar usuário no banco de dados."
            
    except Exception as e:
        print(f"[ATUALIZAR_NOME] Erro: {e}")
        return f"❌ Erro ao atualizar nome: {str(e)}"

@tool("confirmar_pedido")
def confirmar_pedido(state: dict = None) -> str:
    """
    Confirma o pedido atual que está no state.
    SEMPRE use esta ferramenta quando o usuário confirmar um pedido.
    """
    try:
        print(f"[CONFIRMAR_PEDIDO] Confirmando pedido do state")
        
        # Verifica se há um pedido no state
        if not state or "pedido" not in state:
            return "❌ Erro: Nenhum pedido encontrado para confirmar. Faça um pedido primeiro."
        
        pedido = state["pedido"]
        pedido_id = pedido.get("id_pedido")
        valor_total = pedido.get("valor_total", 0)
        
        print(f"[CONFIRMAR_PEDIDO] Pedido encontrado: {pedido_id} - R$ {valor_total}")
        
        # Atualiza o status do pedido no banco
        atualizar_status_pedido(
            pedido_id=pedido_id,
            novo_status="Confirmado - Preparando",
            descricao="Pedido confirmado pelo cliente"
        )
        
        # Atualiza o state
        if state:
            state["status_pedido"] = "pedido_confirmado"
            print(f"[CONFIRMAR_PEDIDO] State atualizado: pedido confirmado")
        
        # Monta mensagem de confirmação
        mensagem = (
            f"✅ *Pedido confirmado com sucesso!*\n\n"
            f"🆔 *ID:* {pedido_id}\n"
            f"💰 *Valor total:* R$ {valor_total:.2f}\n"
            f"📋 *Status:* Preparando\n"
            f"⏱️ *Tempo estimado:* 20-30 minutos\n\n"
            f"🚀 *Seu pedido está sendo preparado!*"
        )
        
        return mensagem
            
    except Exception as e:
        print(f"[CONFIRMAR_PEDIDO] Erro: {e}")
        return f"❌ Erro interno: {str(e)}"

tools = [
    consultar_material_de_apoio,
    processar_pedido_full,
    calcular_entrega,
    processar_retirada,
    criar_cobranca_asaas,
    processar_pagamento_dinheiro,
    atualizar_nome_usuario,
    confirmar_pedido,
    criar_usuario
]
  
class AgentRestaurante:
    def __init__(self):
        self.memory = self._init_memory()
        self.model = self._build_agent()
    
    def _convert_datetime_to_string(self, obj):
        """Converte recursivamente qualquer datetime para string"""
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {key: self._convert_datetime_to_string(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_datetime_to_string(item) for item in obj]
        else:
            return obj
    
    def _prepare_safe_state(self, state: State) -> dict:
        """Prepara o state para serialização segura, removendo objetos não serializáveis"""
        try:
            safe_state = {}
            
            # Copia apenas os campos essenciais do state
            for key, value in state.items():
                if key == "messages":
                    # Pula as mensagens para evitar problemas de serialização
                    continue
                elif key in ["user_info", "pedido", "tipo_entrega", "endereco_entrega", 
                           "forma_pagamento", "valor_troco", "status_pedido"]:
                    # Converte datetime para string nos campos importantes
                    safe_state[key] = self._convert_datetime_to_string(value)
                else:
                    # Copia outros campos simples
                    safe_state[key] = value
            
            return safe_state
            
        except Exception as e:
            print(f"[PREPARE_SAFE_STATE] Erro ao preparar state: {e}")
            # Retorna um state mínimo em caso de erro
            return {
                "user_info": state.get("user_info", {}),
                "pedido": state.get("pedido", {}),
                "status_pedido": state.get("status_pedido", "inicial")
            }
 
    def _init_memory(self):
        memory = MongoDBSaver(coll_memoria)
        return memory
    
    def _build_agent(self):
        graph_builder = StateGraph(State)
        llm = ChatOpenAI(model="gpt-4o-mini", openai_api_key=OPENAI_API_KEY, streaming=True)
        llm_with_tools = llm.bind_tools(tools=tools)
        tool_vector_search = ToolNode(tools=[consultar_material_de_apoio])
        tools_node = ToolNode(tools=tools)

        def chatbot(state: State, config: RunnableConfig) -> State:
            try:
                user_info = state.get("user_info", {})
                nome = user_info.get("nome", "usuário")
                telefone = user_info.get("telefone", "indefinido")
                
                status_pedido = state.get("status_pedido", "inicial")
                pedido_info = ""
                
                if "pedido" in state:
                    pedido = state["pedido"]
                    pedido_info = f"\n\nPEDIDO ATUAL:\n- ID: {pedido.get('id_pedido')}\n- Status: {status_pedido}\n- Valor: R$ {pedido.get('valor_total', 0):.2f}"
                    
                    if state.get("tipo_entrega"):
                        pedido_info += f"\n- Tipo: {state['tipo_entrega']}"
                    
                    if state.get("endereco_entrega"):
                        entrega = state["endereco_entrega"]
                        pedido_info += f"\n- Taxa entrega: R$ {entrega.get('valor_entrega', 0):.2f}"

                # Instrução específica baseada no estado do usuário
                if nome and nome != "usuário" and nome != "None":
                    instrucao_especifica = f"\n\n🚨 INSTRUÇÃO CRÍTICA: O cliente {nome} JÁ ESTÁ IDENTIFICADO! NÃO peça o nome! Cumprimente pelo nome e vá direto para o pedido!"
                else:
                    instrucao_especifica = f"\n\n🚨 INSTRUÇÃO CRÍTICA: O cliente NÃO está identificado! Peça o nome primeiro usando criar_usuario!"
                
                system_prompt = SystemMessage(
                    content=SYSTEM_PROMPT + 
                    f"\n\nCLIENTE ATUAL:\n- Nome: {nome}\n- Telefone: {telefone}" + 
                    pedido_info +
                    instrucao_especifica
                )
                
                # Converte datetime no state para evitar erro de serialização
                try:
                    # Tenta converter datetime no user_info se existir
                    if 'user_info' in state and isinstance(state['user_info'], dict):
                        state['user_info'] = self._convert_datetime_to_string(state['user_info'])
                    
                    response = llm_with_tools.invoke([system_prompt] + state["messages"])
                except Exception as serialization_error:
                    print(f"[DEBUG] Erro de serialização: {serialization_error}")
                    # Se der erro, tenta converter todo o state
                    state_clean = self._convert_datetime_to_string(state)
                    response = llm_with_tools.invoke([system_prompt] + state_clean["messages"])

            except Exception as e:
                print(f"[ERRO chatbot]: {e}")
                raise

            return {
                **state,  # Preserva todo o estado anterior
                "messages": state["messages"] + [response]
            }

        # Wrapper customizado que passa o state para as tools de forma segura
        def safe_tool_node(state: State) -> State:
            """ToolNode customizado que passa o state para as tools sem quebrar serialização"""
            try:
                messages = state.get("messages", [])
                if not messages:
                    return state
                
                last_message = messages[-1]
                if not hasattr(last_message, 'tool_calls') or not last_message.tool_calls:
                    return state
                
                tool_messages = []
                
                for tool_call in last_message.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]
                    
                    # Encontra a tool correspondente
                    tool_func = None
                    for tool in tools:
                        if tool.name == tool_name:
                            tool_func = tool
                            break
                    
                    if tool_func:
                        try:
                            # Prepara o state para serialização segura
                            safe_state = self._prepare_safe_state(state)
                            
                            # Adiciona o state aos argumentos da tool se ela aceita
                            if "state" in tool_func.func.__code__.co_varnames:
                                tool_args["state"] = safe_state
                            
                            # Executa a tool
                            result = tool_func.invoke(tool_args)
                            
                            # Cria ToolMessage de forma segura
                            from langchain_core.messages import ToolMessage
                            tool_message = ToolMessage(
                                content=str(result) if result else "Executado com sucesso",
                                tool_call_id=tool_call["id"],
                                name=tool_name
                            )
                            tool_messages.append(tool_message)
                            
                        except Exception as e:
                            print(f"[SAFE_TOOL_NODE] Erro ao executar {tool_name}: {e}")
                            from langchain_core.messages import ToolMessage
                            error_message = ToolMessage(
                                content=f"Erro: {str(e)}",
                                tool_call_id=tool_call["id"],
                                name=tool_name
                            )
                            tool_messages.append(error_message)
                
                return {
                    **state,
                    "messages": state["messages"] + tool_messages
                }
                
            except Exception as e:
                print(f"[SAFE_TOOL_NODE] Erro geral: {e}")
                return state
        
        tools_node = safe_tool_node

        graph_builder.add_node("entrada_usuario", RunnableLambda(lambda state: state))
        graph_builder.add_node("check_user_role", RunnableLambda(check_user))
        graph_builder.add_node("chatbot", chatbot)
        graph_builder.add_node("tools", tools_node)

        # Ordem de fluxo
        graph_builder.set_entry_point("entrada_usuario")
        graph_builder.add_edge("entrada_usuario", "check_user_role")
        graph_builder.add_edge("check_user_role", "chatbot")
        
        graph_builder.add_conditional_edges(
            "chatbot",
            tools_condition,
            {"tools": "tools", "__end__": END}
        )
        graph_builder.add_edge("tools", "chatbot")

        memory = MongoDBSaver(coll_memoria)
        graph = graph_builder.compile(checkpointer=memory)
        return graph

    def memory_agent(self):
        return self.model