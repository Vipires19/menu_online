# menu/webhook_views.py

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import json
import re
import logging
from .admin_views import atualizar_status_pedido_global
from .services import whatsapp_service

logger = logging.getLogger(__name__)

@csrf_exempt
@require_http_methods(["POST"])
def webhook_asaas_pagamento(request):
    """
    Webhook para receber notificações de pagamento do Asaas
    Integra com o sistema de notificações WhatsApp
    """
    try:
        data = json.loads(request.body)
        logger.info(f"Webhook Asaas recebido: {data}")
        
        # Só processa pagamento confirmado
        if data.get("event") != "PAYMENT_RECEIVED":
            logger.info(f"Evento ignorado: {data.get('event')}")
            return JsonResponse({"status": "ignored"}), 200
        
        # Extrair informações do pagamento
        payment = data.get("payment", {})
        description = payment.get("description", "")
        
        # Exemplo: "Pedido #d815e354 - Vinícius - (11)91234-5678 - Pirão Burger"
        pattern = r"Pedido\s+#(\w+)\s*-\s*(.*?)\s*-\s*(.*?)\s*-"
        match = re.search(pattern, description)
        
        if not match:
            logger.warning(f"Formato inesperado de description: {description}")
            return JsonResponse({
                "status": "error", 
                "message": "Formato inválido de description"
            }), 400
        
        # Extrair dados do pedido
        id_pedido = match.group(1).strip()
        nome_cliente = match.group(2).strip()
        telefone = match.group(3).strip()
        valor_pago = payment.get("value", 0)
        
        # Formatar telefone para padrão internacional
        telefone_formatado = telefone.replace("(", "").replace(")", "").replace("-", "").replace(" ", "")
        if not telefone_formatado.startswith("55"):
            telefone_formatado = "55" + telefone_formatado
        
        logger.info(f"Processando pagamento - Pedido: {id_pedido}, Cliente: {nome_cliente}, Telefone: {telefone_formatado}")
        
        # Atualizar status do pedido para "Enviado para cozinha"
        sucesso_atualizacao = atualizar_status_pedido_global(
            pedido_id=id_pedido,
            novo_status="Enviado para cozinha",
            enviar_notificacao=True
        )
        
        if sucesso_atualizacao:
            logger.info(f"✅ Pedido {id_pedido} atualizado com sucesso")
            
            # Enviar notificação de pagamento confirmado
            try:
                sucesso_notificacao = whatsapp_service.enviar_notificacao_pagamento_confirmado(
                    pedido_id=id_pedido,
                    cliente_nome=nome_cliente,
                    cliente_telefone=telefone_formatado,
                    valor_total=valor_pago
                )
                
                if sucesso_notificacao:
                    logger.info(f"✅ Notificação de pagamento enviada para {nome_cliente}")
                else:
                    logger.warning(f"❌ Falha ao enviar notificação de pagamento para {nome_cliente}")
                    
            except Exception as e:
                logger.error(f"❌ Erro ao enviar notificação de pagamento: {str(e)}")
            
            return JsonResponse({"status": "success"}), 200
        else:
            logger.error(f"❌ Falha ao atualizar pedido {id_pedido}")
            return JsonResponse({
                "status": "error", 
                "message": "Falha ao atualizar pedido"
            }), 500
            
    except json.JSONDecodeError:
        logger.error("❌ Erro ao decodificar JSON do webhook")
        return JsonResponse({
            "status": "error", 
            "message": "JSON inválido"
        }), 400
    except Exception as e:
        logger.error(f"❌ Erro no webhook Asaas: {str(e)}")
        return JsonResponse({
            "status": "error", 
            "message": str(e)
        }), 500

@csrf_exempt
@require_http_methods(["POST"])
def webhook_whatsapp_status(request):
    """
    Webhook para receber atualizações de status via WhatsApp
    Útil para integração com sistemas externos
    """
    try:
        data = json.loads(request.body)
        logger.info(f"Webhook WhatsApp status recebido: {data}")
        
        # Extrair dados necessários
        pedido_id = data.get("pedido_id")
        novo_status = data.get("status")
        enviar_notificacao = data.get("enviar_notificacao", True)
        
        if not pedido_id or not novo_status:
            return JsonResponse({
                "status": "error",
                "message": "pedido_id e status são obrigatórios"
            }), 400
        
        # Atualizar status do pedido
        sucesso = atualizar_status_pedido_global(
            pedido_id=pedido_id,
            novo_status=novo_status,
            enviar_notificacao=enviar_notificacao
        )
        
        if sucesso:
            return JsonResponse({
                "status": "success",
                "message": f"Status do pedido {pedido_id} atualizado para {novo_status}"
            }), 200
        else:
            return JsonResponse({
                "status": "error",
                "message": "Falha ao atualizar status do pedido"
            }), 500
            
    except json.JSONDecodeError:
        return JsonResponse({
            "status": "error",
            "message": "JSON inválido"
        }), 400
    except Exception as e:
        logger.error(f"❌ Erro no webhook WhatsApp status: {str(e)}")
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }), 500

@csrf_exempt
@require_http_methods(["GET"])
def health_check(request):
    """
    Endpoint de health check para verificar se o serviço está funcionando
    """
    try:
        # Verificar se o serviço WhatsApp está respondendo
        from django.conf import settings
        import requests
        
        waha_url = getattr(settings, 'WAHA_API_URL', 'http://waha:3000')
        session_name = getattr(settings, 'WAHA_SESSION_NAME', 'restaurante')
        
        # Tentar fazer uma requisição simples para verificar se a API está online
        response = requests.get(f"{waha_url}/api/sessions", timeout=5)
        
        if response.status_code == 200:
            return JsonResponse({
                "status": "healthy",
                "whatsapp_api": "online",
                "waha_url": waha_url,
                "session_name": session_name
            }), 200
        else:
            return JsonResponse({
                "status": "unhealthy",
                "whatsapp_api": "offline",
                "waha_url": waha_url,
                "session_name": session_name
            }), 503
            
    except Exception as e:
        return JsonResponse({
            "status": "unhealthy",
            "error": str(e)
        }), 503
