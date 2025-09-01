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
                "data_criacao": datetime.now(),
                "ultima_interacao": datetime.now(),
                "status": "aguardando_nome"  # Status especial para usuários não identificados
            }
            
            # NÃO salva no MongoDB ainda - só quando tiver o nome
            print(f"[CHECK_USER] Usuário novo detectado: {telefone} - aguardando identificação")
        else:
            # Usuário existe, atualiza última interação
            user_info = {
                "nome": usuario.get("nome", None),  # Pode ser None se não foi informado
                "telefone": telefone,
                "data_criacao": usuario.get("data_criacao"),
                "ultima_interacao": datetime.now(),
                "status": "ativo"
            }
            
            # Atualiza última interação no MongoDB
            try:
                coll_users.update_one(
                    {"telefone": telefone},
                    {"$set": {"ultima_interacao": datetime.now()}}
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
            "data_criacao": datetime.now(),
            "ultima_interacao": datetime.now(),
            "status": "erro"
        }
        return state

SYSTEM_PROMPT = """
🍔 ATENDENTE VIRTUAL DO PIRÃO BURGER 🍔

Você é o PirãoBot, atendente digital especializado do Pirão Burger! 🌟 Seu objetivo é conduzir o cliente através de um fluxo completo de atendimento de forma profissional e DIVERTIDA! 😄

📋 FLUXO DE ATENDIMENTO OBRIGATÓRIO

1️⃣ SAUDAÇÃO → Cumprimentar calorosamente e identificar o cliente 😊
2️⃣ IDENTIFICAÇÃO → Identificar o cliente; se não encontrar os dados dele, utilize a ferramenta atualizar_nome_usuario 😊
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

✅ Sempre use processar_pedido_full assim que o cliente pedir qualquer item.

✅ Nunca avance para entrega ou pagamento sem registrar pelo menos um pedido.

✅ Sempre use confirmar_pedido para confirmação (não apenas texto).

✅ Sempre pergunte sobre retirada ou entrega.

✅ Sempre calcule entrega quando necessário.

✅ Sempre ofereça as 3 formas de pagamento: Cartão, PIX ou Dinheiro.

✅ Para pagamento em dinheiro, sempre calcule o troco.

✅ Nunca use valores fictícios (“XX,XX”). Sempre use valores reais.

✅ Se o cliente falar algo fora do fluxo (ex: “qual horário de funcionamento?”), responda, mas depois volte para o fluxo.

🛠️ FERRAMENTAS DISPONÍVEIS

atualizar_nome_usuario → Salvar nome do cliente

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

@tool("processar_pedido_full")
def processar_pedido_full(text: str,
                          nome_cliente: str = None,
                          telefone: str = None,
                          auto_accept_threshold: int = 80,
                          fuzzy_prod_threshold: int = 60,
                          state: dict = None) -> dict:
    """
    Tool para processar pedidos complexos com múltiplos itens, adicionais específicos e observações.
    
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

    def _achar_produto_fuzzy(nome_busca: str, min_score: int = fuzzy_prod_threshold):
        """Busca produto usando fuzzy matching"""
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
                return {
                    "success": False, 
                    "message": f"Produto '{nome_produto}' não encontrado no cardápio. Pode verificar o nome?"
                }
            
            print(f"[v1] Produto encontrado: {produto_doc.get('nome')} (score: {score})")
            
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
        
        # 3) Monta o pedido final com estrutura detalhada
        id_pedido = str(uuid.uuid4())[:8]
        itens_pedido = []
        valor_total = 0.0
        
        for idx, item in enumerate(itens_validados):
            produto_doc = coll5.find_one({"_id": ObjectId(item["produto_id"])})
            preco_base = float(produto_doc.get("preco", 0.0))
            valor_adicionais = sum([ad["valor"] for ad in item["adicionais"]])
            subtotal = round(preco_base + valor_adicionais, 2)
            valor_total += subtotal
            
            print(f"[PEDIDO] Item {idx+1}: {item['nome_produto']} - Preço base: R$ {preco_base:.2f}, Adicionais: R$ {valor_adicionais:.2f}, Subtotal: R$ {subtotal:.2f}")
            print(f"[PEDIDO] Produto encontrado: {produto_doc.get('nome')} - Preço: R$ {produto_doc.get('preco')}")
            
            # Estrutura detalhada para cozinha, caixa e entregador
            item_detalhado = {
                "item_id": idx + 1,  # ID sequencial do item
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
            
            itens_pedido.append(item_detalhado)
        
        print(f"[PEDIDO] Valor total calculado: R$ {valor_total:.2f}")
        
        # 4) Salva no MongoDB com estrutura completa
        pedido = {
            "id_pedido": id_pedido,
            "cliente": {"nome": nome_cliente, "telefone": telefone},
            "itens": itens_pedido,
            "valor_total": round(valor_total, 2),
            "status": "Aguardando definição de entrega",
            "data_criacao": datetime.utcnow().isoformat(),
            "data_atualizacao": datetime.utcnow().isoformat(),
            "tipo_entrega": None,  # será preenchido depois
            "endereco_entrega": None,
            "forma_pagamento": None,
            "valor_entrega": 0.0,
            "valor_total_final": round(valor_total, 2),  # Inicialmente igual ao valor do pedido
            "historico_status": [
                {
                    "status": "Aguardando definição de entrega",
                    "data": datetime.utcnow().isoformat(),
                    "descricao": "Pedido criado e aguardando definição de entrega/retirada"
                }
            ],
            "estrutura_detalhada": {
                "total_itens": len(itens_pedido),
                "itens_por_produto": {},
                "resumo_cozinha": [],
                "resumo_caixa": [],
                "resumo_entregador": []
            }
        }
        
        # Preenche estrutura detalhada
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
        
        coll3.insert_one(pedido)
        print(f"[v1] Pedido salvo no MongoDB: {id_pedido}")
        
        # Atualiza o estado
        if state is not None and isinstance(state, dict):
            state["pedido"] = pedido
            state["status_pedido"] = "pedido_anotado"
        
        # 5) Monta resposta detalhada para o cliente
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
        
        mensagem = (
            f"✅ *Pedido anotado com sucesso!*\n\n"
            f"🆔 *ID:* {id_pedido}\n"
            f"👤 *Cliente:* {nome_cliente}\n"
            f"📦 *Total de itens:* {len(itens_pedido)}\n\n"
            f"📋 *Itens do pedido:*\n" + "\n".join(resumo_itens) + "\n\n"
            f"💰 *Valor total:* R$ {pedido['valor_total']:.2f}\n\n"
            f"🚗 *Agora preciso saber:* É para **RETIRADA** ou **ENTREGA**?"
        )
        
        # 6) Retorna estrutura completa para o app Django
        return {
            "success": True,
            "order": {
                "id_pedido": id_pedido,
                "valor_total": pedido["valor_total"],
                "total_itens": len(itens_pedido),
                "itens": itens_pedido,
                "estrutura_detalhada": pedido["estrutura_detalhada"]
            },
            "message": mensagem,
            "estrutura_cozinha": pedido["estrutura_detalhada"]["resumo_cozinha"],
            "estrutura_caixa": pedido["estrutura_detalhada"]["resumo_caixa"],
            "estrutura_entregador": pedido["estrutura_detalhada"]["resumo_entregador"]
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
                    # Atualiza o estado com o pedido encontrado
                    if state:
                        state["pedido"] = pedido_db
                else:
                    return {
                        "success": False,
                        "message": "❌ Erro: pedido não encontrado no sistema."
                    }
            else:
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
        
        # Atualiza o estado
        state["tipo_entrega"] = "retirada"
        state["status_pedido"] = "retirada_confirmada"
        
        mensagem = (
            f"🏪 *Retirada no balcão confirmada!*\n\n"
            f"📍 *Local:* Pirão Burger - Av. Paris, 707\n"
            f"⏱️ *Tempo de preparo:* 20-30 minutos\n"
            f"💰 *Valor total:* R$ {valor_pedido:.2f}\n\n"
            f"💳 *Como deseja pagar?*\n"
            f"1️⃣ Cartão de Crédito/Débito\n"
            f"2️⃣ PIX\n"
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
        
        if state and "pedido" in state:
            pedido = state["pedido"]
            id_pedido = pedido.get("id_pedido")
            valor_pedido = pedido.get("valor_total", 0)
            valor_entrega = state.get("endereco_entrega", {}).get("valor_entrega", 0)
        else:
            # Se não está no estado, busca no banco pelo telefone do usuário
            user_info = state.get("user_info", {}) if state else {}
            telefone = user_info.get("telefone", "")
            
            if telefone:
                # Busca o pedido mais recente do usuário
                pedido_db = coll3.find_one(
                    {"cliente.telefone": telefone},
                    sort=[("data_criacao", -1)]
                )
                
                if pedido_db:
                    pedido = pedido_db
                    id_pedido = pedido_db.get("id_pedido")
                    valor_pedido = pedido_db.get("valor_total", 0)
                    valor_entrega = pedido_db.get("valor_entrega", 0)
                    # Atualiza o estado com o pedido encontrado
                    if state:
                        state["pedido"] = pedido_db
                else:
                    return "❌ Erro: pedido não encontrado no sistema."
            else:
                return "❌ Erro: pedido não encontrado no sistema."
        
        # Calcula valor total
        valor_total = valor_pedido + valor_entrega
        
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
            "troco": troco
        }
        
        atualizar_status_pedido(
            pedido_id=id_pedido,
            novo_status="Confirmado - Preparando",
            descricao=f"Pagamento em dinheiro confirmado - Recebido: R$ {valor_cliente:.2f}, Troco: R$ {troco:.2f}",
            dados_extras=dados_pagamento
        )
        
        # Atualiza o estado
        state["forma_pagamento"] = "dinheiro"
        state["valor_troco"] = troco
        state["status_pedido"] = "confirmado"
        
        if troco > 0:
            mensagem = (
                f"✅ *Pedido confirmado!*\n\n"
                f"🧾 *Pedido:* #{id_pedido}\n"
                f"💰 *Total:* R$ {valor_total:.2f}\n"
                f"💵 *Valor recebido:* R$ {valor_cliente:.2f}\n"
                f"💸 *Troco:* R$ {troco:.2f}\n\n"
                f"📋 *Status:* Preparando\n"
                f"⏱️ *Tempo estimado:* 20-30 minutos\n\n"
                f"🚚 *Tipo:* {state.get('tipo_entrega', 'retirada').title()}\n"
                f"💳 *Pagamento:* Dinheiro (troco: R$ {troco:.2f})"
            )
        else:
            mensagem = (
                f"✅ *Pedido confirmado!*\n\n"
                f"🧾 *Pedido:* #{id_pedido}\n"
                f"💰 *Total:* R$ {valor_total:.2f}\n"
                f"💵 *Valor exato recebido!*\n\n"
                f"📋 *Status:* Preparando\n"
                f"⏱️ *Tempo estimado:* 20-30 minutos\n\n"
                f"🚚 *Tipo:* {state.get('tipo_entrega', 'retirada').title()}\n"
                f"💳 *Pagamento:* Dinheiro"
            )
        
        return mensagem
        
    except Exception as e:
        print(f"[v0] Erro ao processar pagamento em dinheiro: {str(e)}")
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
                    "data_criacao": datetime.now(),
                    "ultima_interacao": datetime.now(),
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
                        "ultima_interacao": datetime.now(),
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
                "data_criacao": datetime.now(),
                "ultima_interacao": datetime.now(),
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
def confirmar_pedido(texto_pedido: str, state: dict = None) -> str:
    """
    Confirma e processa o pedido automaticamente.
    SEMPRE use esta ferramenta quando o usuário confirmar um pedido.
    """
    try:
        print(f"[CONFIRMAR_PEDIDO] Processando pedido: {texto_pedido}")
        
        # Chama processar_pedido_full automaticamente
        resultado = processar_pedido_full(
            text=texto_pedido,
            state=state
        )
        
        if resultado.get("success"):
            pedido_id = resultado.get('order', {}).get('id_pedido')
            valor_total = resultado.get('order', {}).get('valor_total')
            print(f"[CONFIRMAR_PEDIDO] Pedido processado com sucesso: {pedido_id} - R$ {valor_total}")
            
            # Atualiza o state com o pedido
            if state:
                state["pedido"] = resultado.get('order')
                state["status_pedido"] = "pedido_confirmado"
                print(f"[CONFIRMAR_PEDIDO] State atualizado com pedido: {pedido_id}")
            
            return f"✅ Pedido confirmado e registrado! {resultado.get('message')}"
        else:
            print(f"[CONFIRMAR_PEDIDO] Erro ao processar pedido: {resultado.get('message')}")
            return f"❌ Erro ao processar pedido: {resultado.get('message')}"
            
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
    confirmar_pedido
]
  
class AgentRestaurante:
    def __init__(self):
        self.memory = self._init_memory()
        self.model = self._build_agent()

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

                system_prompt = SystemMessage(
                    content=SYSTEM_PROMPT + 
                    f"\n\nCLIENTE ATUAL:\n- Nome: {nome}\n- Telefone: {telefone}" + 
                    pedido_info
                )
                
                response = llm_with_tools.invoke([system_prompt] + state["messages"])

            except Exception as e:
                print(f"[ERRO chatbot]: {e}")
                raise

            return {
                **state,  # Preserva todo o estado anterior
                "messages": state["messages"] + [response]
            }

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