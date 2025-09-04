# menu/services.py

import requests
import time
import random
import logging
from django.conf import settings
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class WhatsAppNotificationService:
    """
    Serviço para enviar notificações via WhatsApp usando a API Waha
    """
    
    def __init__(self):
        self.api_url = getattr(settings, 'WAHA_API_URL', 'http://waha:3000')
        self.session_name = getattr(settings, 'WAHA_SESSION_NAME', 'restaurante')
        self.timeout = getattr(settings, 'WAHA_TIMEOUT', 30)
    
    def formatar_telefone(self, telefone: str) -> str:
        """
        Formata o telefone para o padrão internacional do WhatsApp
        """
        # Remove caracteres especiais
        telefone_limpo = telefone.replace("(", "").replace(")", "").replace("-", "").replace(" ", "")
        
        # Adiciona DDI do Brasil se não tiver
        if not telefone_limpo.startswith("55"):
            telefone_limpo = "55" + telefone_limpo
        
        return telefone_limpo + "@c.us"
    
    def verificar_numero_existe(self, telefone: str) -> Optional[str]:
        """
        Verifica se o número existe no WhatsApp e retorna o chat_id
        """
        try:
            telefone_formatado = self.formatar_telefone(telefone)
            url = f"{self.api_url}/api/contacts/check-exists"
            params = {
                'phone': telefone_formatado.replace('@c.us', ''),
                'session': self.session_name
            }
            
            response = requests.get(url, params=params, timeout=self.timeout)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("numberExists"):
                    chat_id = data.get("chatId")
                    logger.info(f"✅ Número {telefone} encontrado no WhatsApp: {chat_id}")
                    return chat_id
                else:
                    logger.warning(f"❌ Número {telefone} não registrado no WhatsApp")
                    return None
            else:
                logger.error(f"⚠️ Erro na verificação do número {telefone}: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Erro ao verificar número {telefone}: {str(e)}")
            return None
    
    def enviar_mensagem(self, chat_id: str, mensagem: str) -> bool:
        """
        Envia uma mensagem para o WhatsApp
        """
        try:
            url = f"{self.api_url}/api/sendText"
            headers = {'Content-Type': 'application/json'}
            payload = {
                'session': self.session_name,
                'chatId': chat_id,
                'text': mensagem
            }
            
            response = requests.post(
                url=url,
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                logger.info(f"✅ Mensagem enviada com sucesso para {chat_id}")
                return True
            else:
                logger.error(f"❌ Erro ao enviar mensagem: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Erro ao enviar mensagem para {chat_id}: {str(e)}")
            return False
    
    def simular_digitacao(self, chat_id: str, duracao: int = 3) -> bool:
        """
        Simula digitação no WhatsApp
        """
        try:
            # Iniciar digitação
            start_url = f"{self.api_url}/api/startTyping"
            start_payload = {
                'session': self.session_name,
                'chatId': chat_id
            }
            
            requests.post(start_url, json=start_payload, timeout=self.timeout)
            
            # Aguardar um tempo aleatório
            time.sleep(random.randint(2, duracao))
            
            # Parar digitação
            stop_url = f"{self.api_url}/api/stopTyping"
            stop_payload = {
                'session': self.session_name,
                'chatId': chat_id
            }
            
            requests.post(stop_url, json=stop_payload, timeout=self.timeout)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Erro ao simular digitação para {chat_id}: {str(e)}")
            return False
    
    def enviar_notificacao_status_pedido(self, pedido_id: str, cliente_nome: str, 
                                        cliente_telefone: str, status_anterior: str, 
                                        novo_status: str, valor_total: float = None, 
                                        tipo_entrega: str = None) -> bool:
        """
        Envia notificação de mudança de status do pedido
        """
        try:
            # Verificar se o número existe no WhatsApp
            chat_id = self.verificar_numero_existe(cliente_telefone)
            if not chat_id:
                logger.warning(f"Número {cliente_telefone} não encontrado no WhatsApp")
                return False
            
            # Criar mensagem personalizada baseada no status
            mensagem = self._criar_mensagem_status_pedido(
                pedido_id, cliente_nome, status_anterior, novo_status, valor_total, tipo_entrega
            )
            
            # Simular digitação
            self.simular_digitacao(chat_id)
            
            # Enviar mensagem
            return self.enviar_mensagem(chat_id, mensagem)
            
        except Exception as e:
            logger.error(f"❌ Erro ao enviar notificação de status: {str(e)}")
            return False
    
    def _criar_mensagem_status_pedido(self, pedido_id: str, cliente_nome: str, 
                                     status_anterior: str, novo_status: str, 
                                     valor_total: float = None, tipo_entrega: str = None) -> str:
        """
        Cria mensagem personalizada baseada no status do pedido
        """
        # Emojis para cada status
        status_emojis = {
            'Enviado para cozinha': '👨‍🍳',
            'Em preparo': '🔥',
            'Pronto': '✅',
            'Saiu para entrega': '🚚',
            'Retirada': '🏪',
            'Balcão': '🏪',
            'Concluído': '🎉',
            'Cancelado': '❌'
        }
        
        emoji = status_emojis.get(novo_status, '📱')
        
        # Mensagem base
        mensagem = f"*{emoji} Atualização do Pedido #{pedido_id}*\n\n"
        mensagem += f"Olá *{cliente_nome}*! 👋\n\n"
        
        # Mensagens específicas por status
        if novo_status == 'Enviado para cozinha':
            mensagem += "Seu pedido foi *enviado para a cozinha* e está sendo preparado! 👨‍🍳\n"
            mensagem += "Em breve você receberá mais atualizações! ⏰\n\n"
            
        elif novo_status == 'Em preparo':
            mensagem += "Seu pedido está sendo *preparado* com muito carinho! 🔥\n"
            mensagem += "Nossa equipe está trabalhando para entregar o melhor sabor! 😋\n\n"
            
        elif novo_status == 'Pronto':
            mensagem += "🎉 *Seu pedido está PRONTO!* 🎉\n\n"
            if tipo_entrega and tipo_entrega.lower() == 'entrega':
                mensagem += "Nosso entregador já está a caminho! 🚚\n"
            else:
                mensagem += "Pode retirar no balcão! 🏪\n"
            mensagem += "Obrigado pela preferência! ❤️\n\n"
            
        elif novo_status == 'Saiu para entrega':
            mensagem += "🚚 *Seu pedido saiu para entrega!*\n\n"
            mensagem += "Nosso entregador está a caminho do seu endereço! 📍\n"
            mensagem += "Em breve você receberá seu pedido! ⏰\n\n"
            
        elif novo_status == 'Concluído':
            mensagem += "🎉 *Pedido entregue com sucesso!* 🎉\n\n"
            mensagem += "Esperamos que tenha gostado! 😋\n"
            mensagem += "Obrigado por escolher o Pirão Burger! ❤️\n"
            mensagem += "Volte sempre! 🍔\n\n"
            
        elif novo_status == 'Cancelado':
            mensagem += "❌ *Seu pedido foi cancelado*\n\n"
            mensagem += "Sentimos muito pelo inconveniente! 😔\n"
            mensagem += "Entre em contato conosco se precisar de ajuda! 📞\n\n"
        
        # Adicionar valor total se disponível
        if valor_total:
            mensagem += f"💰 *Valor total: R$ {valor_total:.2f}*\n\n"
        
        # Rodapé
        mensagem += "---\n"
        mensagem += "🍔 *Pirão Burger*\n"
        mensagem += "📱 WhatsApp: (11) 99999-9999\n"
        mensagem += "⏰ Funcionamento: 18h às 23h"
        
        return mensagem
    
    def enviar_notificacao_pagamento_confirmado(self, pedido_id: str, cliente_nome: str, 
                                               cliente_telefone: str, valor_total: float) -> bool:
        """
        Envia notificação de pagamento confirmado
        """
        try:
            chat_id = self.verificar_numero_existe(cliente_telefone)
            if not chat_id:
                return False
            
            mensagem = (
                f"*Pagamento confirmado!* 🎉\n\n"
                f"✅ Pedido *#{pedido_id}*\n"
                f"👤 Cliente: *{cliente_nome}*\n"
                f"💰 Valor: *R$ {valor_total:.2f}*\n\n"
                f"Obrigado por comprar no Pirão Burger! 🍔\n"
                f"Seu pedido já foi encaminhado para cozinha! 🔥\n\n"
                f"---\n"
                f"🍔 *Pirão Burger*\n"
                f"📱 WhatsApp: (11) 99999-9999"
            )
            
            self.simular_digitacao(chat_id)
            return self.enviar_mensagem(chat_id, mensagem)
            
        except Exception as e:
            logger.error(f"❌ Erro ao enviar notificação de pagamento: {str(e)}")
            return False


# Instância global do serviço
whatsapp_service = WhatsAppNotificationService()
