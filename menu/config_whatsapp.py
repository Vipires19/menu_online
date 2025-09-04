# menu/config_whatsapp.py
# Configuração automática do WhatsApp baseada no ambiente

import os
import socket
from django.conf import settings

def detect_environment():
    """
    Detecta se está rodando local ou no Docker
    """
    try:
        # Tentar resolver o hostname 'waha' (só funciona no Docker)
        socket.gethostbyname('waha')
        return 'docker'
    except socket.gaierror:
        return 'local'

def get_waha_url():
    """
    Retorna a URL correta do Waha baseada no ambiente
    """
    environment = detect_environment()
    
    if environment == 'docker':
        return 'http://waha:3000'
    else:
        # Ambiente local - Waha rodando no Docker
        return 'http://localhost:3000'

def get_mongo_url():
    """
    Retorna a URL correta do MongoDB baseada no ambiente
    """
    environment = detect_environment()
    
    if environment == 'docker':
        return 'mongodb://admin:senha123@mongodb:27017/restaurante_db?authSource=admin'
    else:
        # Ambiente local - MongoDB rodando no Docker
        return 'mongodb://admin:senha123@localhost:27017/restaurante_db?authSource=admin'

def get_django_url():
    """
    Retorna a URL do Django para webhooks
    """
    environment = detect_environment()
    
    if environment == 'docker':
        return 'http://django:8000'
    else:
        # Ambiente local
        return 'http://localhost:8000'

# Configurações automáticas
WAHA_API_URL = get_waha_url()
MONGO_URL = get_mongo_url()
DJANGO_URL = get_django_url()

print(f"🔍 Ambiente detectado: {detect_environment()}")
print(f"📱 URL do Waha: {WAHA_API_URL}")
print(f"🗄️ URL do MongoDB: {MONGO_URL}")
print(f"🌐 URL do Django: {DJANGO_URL}")
