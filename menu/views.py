from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, JsonResponse
from django.core.paginator import Paginator
from django.views.decorators.http import require_http_methods
from mongoengine import DoesNotExist
from .models import Produto, Pedido, Adicional
import json
from .forms import ProdutoForm, AdicionalForm

# Create your views here.


def lista_produtos(request):
    """
    View que exibe a lista de produtos do cardápio
    Usa as capacidades de busca do MongoDB
    """
    # Parâmetros de filtro
    categoria = request.GET.get('categoria')
    busca = request.GET.get('busca', '').strip()
    
    # Busca produtos usando o método personalizado
    produtos = Produto.buscar_produtos(
        termo_busca=busca if busca else None,
        categoria=categoria,
        apenas_disponiveis=True
    )
    
    # Converte QuerySet do MongoEngine para lista para paginação
    produtos_list = list(produtos)
    
    # Paginação - 12 produtos por página
    paginator = Paginator(produtos_list, 12)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Busca categorias disponíveis
    categorias = Produto.get_categorias()
    
    context = {
        'produtos': page_obj,
        'categorias': categorias,
        'categoria_atual': categoria,
        'busca_atual': busca,
        'total_produtos': len(produtos_list),
    }
    
    return render(request, 'cardapio/lista_produtos.html', context)

def produto_detalhe(request, produto_id):
    # Busca o produto pelo ObjectId (MongoEngine)
    try:
        produto = Produto.objects.get(id=produto_id)
    except Produto.DoesNotExist:
        raise Http404("Produto não encontrado")
    
    # Adicionais disponíveis
    adicionais = produto.adicionais if hasattr(produto, 'adicionais') else []
    
    # Produtos da mesma categoria, exceto o atual
    produtos_relacionados = Produto.objects(
        categoria=produto.categoria,
        id__ne=produto.id
    ).order_by('nome')[:4]  # limitar a 4 itens, por exemplo
    
    context = {
        'produto': produto,
        'adicionais': adicionais,
        'produtos_relacionados': produtos_relacionados
    }
    
    return render(request, 'cardapio/produto_detalhe.html', context)

@require_http_methods(["POST"])
def adicionar_ao_carrinho(request):
    """
    View AJAX para adicionar produto ao carrinho
    Demonstra como trabalhar com JSON no MongoDB
    """
    try:
        data = json.loads(request.body)
        produto_id = data.get('produto_id')
        quantidade = int(data.get('quantidade', 1))
        adicionais_selecionados = data.get('adicionais', [])
        
        # Busca o produto
        produto = Produto.objects.get(id=produto_id, disponivel=True)
        
        # Calcula o preço total
        preco_total = produto.preco * quantidade
        
        # Adiciona preço dos adicionais
        for adicional_nome in adicionais_selecionados:
            for adicional in produto.adicionais:
                if adicional.nome == adicional_nome and adicional.disponivel:
                    preco_total += adicional.preco * quantidade
        
        # Aqui você salvaria no carrinho (sessão, banco, etc.)
        # Por enquanto, apenas retorna sucesso
        
        return JsonResponse({
            'success': True,
            'message': 'Produto adicionado ao carrinho!',
            'preco_total': float(preco_total)
        })
        
    except DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Produto não encontrado'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': 'Erro interno do servidor'
        }, status=500)

def buscar_produtos_ajax(request):
    """
    View AJAX para busca em tempo real
    Aproveita os índices do MongoDB para busca rápida
    """
    termo = request.GET.get('q', '').strip()
    
    if len(termo) < 2:
        return JsonResponse({'produtos': []})
    
    # Busca produtos
    produtos = Produto.buscar_produtos(termo_busca=termo).limit(10)
    
    # Serializa os resultados
    resultados = []
    for produto in produtos:
        resultados.append({
            'id': str(produto.id),
            'nome': produto.nome,
            'categoria': produto.categoria,
            'preco': float(produto.preco),
            'url': produto.get_absolute_url()
        })
    
    return JsonResponse({'produtos': resultados})

def produtos_admin_list(request):
    produtos = Produto.objects.all()
    return render(request, "admin/produtos_list.html", {"produtos": produtos})

def produto_admin_form(request, pk=None):
    produto = Produto.objects.get(id=pk) if pk else None
    if request.method == "POST":
        form = ProdutoForm(request.POST, request.FILES, instance=produto)
        if form.is_valid():
            produto = form.save()
            # salvar adicionais inline...
            return redirect('produtos_admin_list')
    else:
        form = ProdutoForm(instance=produto)
    adicionais = Adicional.objects.filter(produto=produto) if produto else []
    return render(request, "admin/produto_form.html", {"form": form, "adicionais": adicionais})

def produto_admin_delete(request, pk):
    produto = get_object_or_404(Produto, id=pk)
    Adicional.objects(produto=produto).delete()
    produto.delete()
    return redirect('produtos_admin_list')