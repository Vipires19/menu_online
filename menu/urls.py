from django.urls import path
from . import views
from . import admin_views
from . import webhook_views
from django.contrib.auth import views as auth_views

# Define as rotas/URLs da aplicação
urlpatterns = [
    # Página principal do cardápio
    path('', views.lista_produtos, name='lista_produtos'),
    path('cardapio_admin/', admin_views.produtos_admin_list, name='produtos_admin_list'),
    path('editar_cardapio/', admin_views.produto_admin_form, name='produto_admin_create'),
    path('editar_cardapio/<str:pk>/editar/', admin_views.produto_admin_form, name='produto_admin_edit'),
    path('editar_cardapio/<str:pk>/deletar/', admin_views.produto_admin_delete, name='produto_admin_delete'),

    # Gestão de Pedidos
    path('pedidos/fila/', admin_views.pedidos_fila_preparo, name='pedidos_fila_preparo'),
    path('pedidos/criar-manual/', admin_views.criar_pedido_manual, name='criar_pedido_manual'),
    path('pedidos/<str:pedido_id>/atualizar-status/', admin_views.atualizar_status_pedido, name='atualizar_status_pedido'),

    #path('adicionais/', admin_views.adicionais_admin_list, name='adicionais_admin_list'),
    #path('adicionais/novo/', admin_views.adicional_admin_form, name='adicional_admin_create'),
    #path('adicionais/<str:pk>/editar/', admin_views.adicional_admin_form, name='adicional_admin_edit'),

    # Login / Logout
    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="/"), name="logout"),

    # Página de detalhes do produto (usa ObjectId do MongoDB)
    path('produto/<str:produto_id>/', views.produto_detalhe, name='produto_detalhe'),
    
    # APIs AJAX
    path('api/adicionar-carrinho/', views.adicionar_ao_carrinho, name='adicionar_carrinho'),
    path('api/buscar/', views.buscar_produtos_ajax, name='buscar_produtos'),
    
    # Dashboard administrativo
    path('dashboard/', admin_views.estatisticas_dashboard, name='dashboard'),
    
    # ========================================
    # WEBHOOKS E APIs EXTERNAS
    # ========================================
    
    # Webhook do Asaas (gateway de pagamento)
    path('webhook/asaas/', webhook_views.webhook_asaas_pagamento, name='webhook_asaas'),
    
    # Webhook para atualizações de status via WhatsApp
    path('webhook/whatsapp/status/', webhook_views.webhook_whatsapp_status, name='webhook_whatsapp_status'),
    
    # Health check para monitoramento
    path('health/', webhook_views.health_check, name='health_check'),
]