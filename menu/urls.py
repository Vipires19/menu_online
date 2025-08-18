from django.urls import path
from . import views
from . import admin_views
from django.contrib.auth import views as auth_views

# Define as rotas/URLs da aplicação
urlpatterns = [
    # Página principal do cardápio
    path('', views.lista_produtos, name='lista_produtos'),
    path('cardapio_admin/', admin_views.produtos_admin_list, name='produtos_admin_list'),
    path('editar_cardapio/', admin_views.produto_admin_form, name='produto_admin_create'),
    path('editar_cardapio/<str:pk>/editar/', admin_views.produto_admin_form, name='produto_admin_edit'),
    path('editar_cardapio/<str:pk>/deletar/', admin_views.produto_admin_delete, name='produto_admin_delete'),

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
    path('dashboard/', views.estatisticas_dashboard, name='dashboard'),
]