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

def estatisticas_dashboard(request):
    """
    Dashboard completo com estatísticas avançadas do restaurante
    Inclui filtros por período e métricas detalhadas
    """
    from datetime import datetime, timedelta
    from .models import PedidoReal
    import json
    
    # Parâmetros de filtro
    periodo = request.GET.get('periodo', 'geral')  # 'hoje', 'mes', 'geral'
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')
    
    # Definir filtros de data baseado no período
    filtro_data = {}
    hoje = datetime.now()
    
    if periodo == 'hoje':
        inicio_dia = hoje.replace(hour=0, minute=0, second=0, microsecond=0)
        fim_dia = hoje.replace(hour=23, minute=59, second=59, microsecond=999999)
        filtro_data = {
            'data_criacao': {
                '$gte': inicio_dia.isoformat(),
                '$lte': fim_dia.isoformat()
            }
        }
    elif periodo == 'mes':
        inicio_mes = hoje.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        filtro_data = {
            'data_criacao': {
                '$gte': inicio_mes.isoformat()
            }
        }
    elif data_inicio and data_fim:
        filtro_data = {
            'data_criacao': {
                '$gte': data_inicio,
                '$lte': data_fim
            }
        }
    
    # Buscar pedidos com filtro
    pedidos_query = PedidoReal.objects
    if filtro_data:
        pedidos_query = pedidos_query(__raw__=filtro_data)
    
    pedidos = list(pedidos_query)
    
    # === ESTATÍSTICAS GERAIS ===
    total_pedidos = len(pedidos)
    pedidos_concluidos = len([p for p in pedidos if p.status == 'Concluído'])
    pedidos_cancelados = len([p for p in pedidos if 'cancelado' in p.status.lower() or 'Cancelado' in p.status])
    pedidos_em_andamento = len([p for p in pedidos if p.status not in ['Concluído', 'Cancelado']])
    
    # === VALORES FINANCEIROS ===
    valor_total_arrecadado = sum([p.valor_total_final or 0 for p in pedidos if p.status == 'Concluído'])
    valor_total_pedidos = sum([p.valor_total_final or 0 for p in pedidos])
    valor_medio_pedido = valor_total_pedidos / total_pedidos if total_pedidos > 0 else 0
    
    # === ESTATÍSTICAS POR STATUS ===
    status_counts = {}
    for pedido in pedidos:
        status = pedido.status  # Usar status direto da estrutura real
        status_counts[status] = status_counts.get(status, 0) + 1
    
    # === ESTATÍSTICAS POR HORA (últimas 24h) ===
    pedidos_por_hora = {}
    if periodo == 'hoje':
        for i in range(24):
            pedidos_por_hora[f"{i:02d}:00"] = 0
        
        for pedido in pedidos:
            try:
                data_pedido = datetime.fromisoformat(pedido.data_criacao.replace('Z', '+00:00'))
                hora = f"{data_pedido.hour:02d}:00"
                pedidos_por_hora[hora] = pedidos_por_hora.get(hora, 0) + 1
            except:
                continue
    
    # === ESTATÍSTICAS POR DIA (últimos 30 dias) ===
    pedidos_por_dia = {}
    if periodo == 'mes':
        for i in range(30):
            data = hoje - timedelta(days=i)
            dia_str = data.strftime('%d/%m')
            pedidos_por_dia[dia_str] = 0
        
        for pedido in pedidos:
            try:
                data_pedido = datetime.fromisoformat(pedido.data_criacao.replace('Z', '+00:00'))
                dia_str = data_pedido.strftime('%d/%m')
                if dia_str in pedidos_por_dia:
                    pedidos_por_dia[dia_str] += 1
            except:
                continue
    
    # === ESTATÍSTICAS DE PRODUTOS ===
    produtos_por_categoria = list(Produto.objects.aggregate([
        {"$match": {"disponivel": True}},
        {"$group": {
            "_id": "$categoria",
            "total": {"$sum": 1},
            "preco_medio": {"$avg": "$preco"}
        }},
        {"$sort": {"total": -1}}
    ]))
    
    # === PRODUTOS MAIS VENDIDOS ===
    produtos_vendidos = {}
    for pedido in pedidos:
        if hasattr(pedido, 'itens') and pedido.itens:
            for item in pedido.itens:
                # Verificar se o item tem o campo 'produto' e não está vazio
                nome_produto = item.get('produto', '')
                if nome_produto and nome_produto.strip() and nome_produto != 'Produto':  # Só adicionar se não estiver vazio e não for o fallback
                    quantidade = item.get('quantidade', 1)
                    produtos_vendidos[nome_produto] = produtos_vendidos.get(nome_produto, 0) + quantidade
    
    produtos_mais_vendidos = sorted(produtos_vendidos.items(), key=lambda x: x[1], reverse=True)[:10]
    
    # === FORMAS DE PAGAMENTO ===
    formas_pagamento = {}
    for pedido in pedidos:
        forma = pedido.forma_pagamento or 'Não informado'
        formas_pagamento[forma] = formas_pagamento.get(forma, 0) + 1
    
    # === TIPOS DE ENTREGA ===
    tipos_entrega = {}
    for pedido in pedidos:
        tipo = pedido.tipo_entrega or 'Não informado'
        tipos_entrega[tipo] = tipos_entrega.get(tipo, 0) + 1
    
    # === TAXA DE CONVERSÃO ===
    taxa_conversao = (pedidos_concluidos / total_pedidos * 100) if total_pedidos > 0 else 0
    
    # === TEMPO MÉDIO DE PREPARO (simulado) ===
    tempo_medio_preparo = 25  # minutos (simulado)
    
    # === CÁLCULOS PARA OS PROGRESS RINGS ===
    taxa_conversao_restante = 100 - taxa_conversao
    tempo_medio_restante = 60 - tempo_medio_preparo
    
    context = {
        # Filtros
        'periodo_atual': periodo,
        'data_inicio': data_inicio,
        'data_fim': data_fim,
        
        # Métricas principais
        'total_pedidos': total_pedidos,
        'pedidos_concluidos': pedidos_concluidos,
        'pedidos_cancelados': pedidos_cancelados,
        'pedidos_em_andamento': pedidos_em_andamento,
        'valor_total_arrecadado': valor_total_arrecadado,
        'valor_total_pedidos': valor_total_pedidos,
        'valor_medio_pedido': valor_medio_pedido,
        'taxa_conversao': taxa_conversao,
        'tempo_medio_preparo': tempo_medio_preparo,
        'taxa_conversao_restante': taxa_conversao_restante,
        'tempo_medio_restante': tempo_medio_restante,
        
        # Estatísticas detalhadas
        'status_counts': status_counts,
        'produtos_por_categoria': produtos_por_categoria,
        'produtos_mais_vendidos': produtos_mais_vendidos,
        'formas_pagamento': formas_pagamento,
        'tipos_entrega': tipos_entrega,
        
        # Dados para gráficos
        'pedidos_por_hora': json.dumps(pedidos_por_hora),
        'pedidos_por_dia': json.dumps(pedidos_por_dia),
        'status_data': json.dumps(list(status_counts.items())),
        'categorias_data': json.dumps([(cat['_id'], cat['total']) for cat in produtos_por_categoria]),
        'produtos_vendidos_data': json.dumps(produtos_mais_vendidos),
        'pagamento_data': json.dumps(list(formas_pagamento.items())),
        'entrega_data': json.dumps(list(tipos_entrega.items())),
        
        # Estatísticas de produtos
        'total_produtos': Produto.objects(disponivel=True).count(),
        'total_categorias': len(produtos_por_categoria),
    }
    
    return render(request, 'admin/dashboard.html', context)

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