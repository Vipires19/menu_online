# menu/admin_views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required as staff_required
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from .forms import ProdutoForm, AdicionalForm
from .models import Produto, Adicional
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.http import JsonResponse
from .models import Pedido, PedidoReal
from datetime import datetime, timedelta
from .services import whatsapp_service

def atualizar_status_pedido_global(pedido_id: str, novo_status: str, enviar_notificacao: bool = True) -> bool:
    """
    Função global para atualizar status de pedidos
    Pode ser chamada de qualquer lugar (webhooks, views, etc.)
    
    Args:
        pedido_id: ID do pedido (string)
        novo_status: Novo status do pedido
        enviar_notificacao: Se deve enviar notificação WhatsApp
    
    Returns:
        bool: True se atualização foi bem-sucedida
    """
    try:
        # Buscar pedido por id_pedido (string) ou ObjectId
        try:
            pedido = PedidoReal.objects.get(id_pedido=pedido_id)
        except PedidoReal.DoesNotExist:
            try:
                pedido = PedidoReal.objects.get(id=pedido_id)
            except PedidoReal.DoesNotExist:
                print(f"❌ Pedido {pedido_id} não encontrado")
                return False
        
        # Status válidos
        status_validos = [
            'Enviado para cozinha', 'Em preparo', 'Pronto', 
            'Saiu para entrega', 'Retirada', 'Balcão', 'Concluído', 'Cancelado'
        ]
        
        if novo_status not in status_validos:
            print(f"❌ Status inválido: {novo_status}")
            return False
        
        # Salvar status anterior
        status_anterior = pedido.status
        
        # Adicionar ao histórico
        historico_entry = {
            'status': novo_status,
            'data': datetime.now().isoformat(),
            'descricao': f'Status alterado para: {novo_status}'
        }
        
        if not pedido.historico_status:
            pedido.historico_status = []
        pedido.historico_status.append(historico_entry)
        
        # Atualizar status
        pedido.status = novo_status
        pedido.data_atualizacao = datetime.now().isoformat()
        pedido.save()
        
        print(f"✅ Status do pedido {pedido_id} atualizado para: {novo_status}")
        
        # Enviar notificação WhatsApp se solicitado
        if enviar_notificacao and pedido.cliente_telefone:
            try:
                sucesso_notificacao = whatsapp_service.enviar_notificacao_status_pedido(
                    pedido_id=pedido.id_pedido,
                    cliente_nome=pedido.cliente_nome,
                    cliente_telefone=pedido.cliente_telefone,
                    status_anterior=status_anterior,
                    novo_status=novo_status,
                    valor_total=pedido.valor_total_final,
                    tipo_entrega=pedido.tipo_entrega
                )
                
                if sucesso_notificacao:
                    print(f"✅ Notificação WhatsApp enviada para {pedido.cliente_nome}")
                else:
                    print(f"❌ Falha ao enviar notificação WhatsApp")
                    
            except Exception as e:
                print(f"❌ Erro ao enviar notificação WhatsApp: {str(e)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Erro ao atualizar pedido {pedido_id}: {str(e)}")
        return False

# Helper para salvar imagem no storage padrão e gravar o caminho em produto.imagem
def _save_uploaded_image_on_produto(produto, uploaded_file):
    """
    Salva uploaded_file usando default_storage e grava o caminho em produto.imagem.
    Retorna o path salvo.
    """
    filename = uploaded_file.name
    saved_path = default_storage.save(f'produtos/{filename}', ContentFile(uploaded_file.read()))
    produto.imagem = saved_path
    return saved_path

@login_required
def produtos_admin_list(request):
    qs = Produto.objects.order_by('nome')
    search = request.GET.get('search', '').strip()
    categoria = request.GET.get('categoria')
    if search:
        qs = qs.filter(nome__icontains=search)
    if categoria:
        qs = qs.filter(categoria=categoria)
    categorias = Produto.objects.distinct('categoria')
    return render(request, 'admin/produtos_list.html', {
        'produtos': qs,
        'categorias': categorias,
        'search': search,
        'categoria': categoria
    })

@login_required
def produto_admin_form(request, pk=None):
    # Busca seguro do produto (pode ser novo)
    produto = None
    if pk:
        try:
            produto = Produto.objects.get(id=pk)
        except Produto.DoesNotExist:
            produto = None

    if request.method == 'POST':
        form = ProdutoForm(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data

            if produto:
                # editar
                produto.nome = data['nome']
                produto.categoria = data['categoria']
                produto.preco = data['preco']
                produto.disponivel = data['disponivel']
                produto.descricao = data['descricao']
                produto.save()
            else:
                # criar
                produto = Produto(
                    nome=data['nome'],
                    categoria=data['categoria'],
                    preco=data['preco'],
                    disponivel=data['disponivel'],
                    descricao=data['descricao']
                )
                produto.save()

            # imagem: salvar via helper e persistir
            if request.FILES.get('imagem'):
                _save_uploaded_image_on_produto(produto, request.FILES['imagem'])
                produto.save()

            # --- tratar adicionais como EmbeddedDocumentListField ---
            nomes = request.POST.getlist('adicional_nome')
            precos = request.POST.getlist('adicional_preco')

            # substitui lista de adicionais do produto
            produto.adicionais = []
            for n, p in zip(nomes, precos):
                if n and n.strip():
                    try:
                        preco_float = float(p)
                    except Exception:
                        preco_float = 0.0
                    produto.adicionais.append(Adicional(nome=n.strip(), preco=preco_float, disponivel=True))

            produto.save()

            return redirect('produtos_admin_list')
    else:
        initial = {}
        if produto:
            initial = {
                'nome': produto.nome,
                'categoria': produto.categoria,
                'preco': produto.preco,
                'disponivel': produto.disponivel,
                'descricao': produto.descricao,
            }
        form = ProdutoForm(initial=initial)

    adicionais = produto.adicionais if produto else []
    return render(request, 'admin/produto_form.html', {
        'form': form,
        'produto': produto,
        'adicionais': adicionais
    })

@login_required
def produto_admin_delete(request, pk):
    try:
        produto = Produto.objects.get(id=pk)  # MongoEngine
    except Produto.DoesNotExist:
        raise Http404("Produto não encontrado")
    
    produto.delete()
    return redirect('produtos_admin_list')

@login_required
def pedidos_fila_preparo(request):
    """
    View para exibir a fila de preparo dos pedidos organizados por status
    """
    # Buscar pedidos que estão na cozinha ou em preparo
    status_cozinha = ['Enviado para cozinha', 'Em preparo', 'Pronto', 'Saiu para entrega', 'Retirada', 'Balcão']
    
    # Query base - pedidos na cozinha
    query = {'status': {'$in': status_cozinha}}
    
    # Buscar por cliente se especificado
    cliente_search = request.GET.get('cliente', '').strip()
    if cliente_search:
        query['cliente.nome'] = {'$regex': cliente_search, '$options': 'i'}
    
    # Buscar por telefone se especificado
    telefone_search = request.GET.get('telefone', '').strip()
    if telefone_search:
        query['cliente.telefone'] = {'$regex': telefone_search, '$options': 'i'}
    
    # Buscar todos os pedidos usando query raw
    todos_pedidos = PedidoReal.objects(__raw__=query).order_by('-data_criacao')
    
    # Organizar pedidos por status
    pedidos_por_status = {
        'Enviado_para_cozinha': [],
        'Em_preparo': [],
        'Pronto': [],
        'Saiu_para_entrega': [],
        'Retirada': [],
        'Balcao': []
    }
    
    for pedido in todos_pedidos:
        if pedido.status == 'Enviado para cozinha':
            pedidos_por_status['Enviado_para_cozinha'].append(pedido)
        elif pedido.status == 'Em preparo':
            pedidos_por_status['Em_preparo'].append(pedido)
        elif pedido.status == 'Pronto':
            pedidos_por_status['Pronto'].append(pedido)
        elif pedido.status == 'Saiu para entrega':
            pedidos_por_status['Saiu_para_entrega'].append(pedido)
        elif pedido.status == 'Retirada':
            pedidos_por_status['Retirada'].append(pedido)
        elif pedido.status == 'Balcão':
            pedidos_por_status['Balcao'].append(pedido)
    
    # Estatísticas para o dashboard
    stats = {
        'total_pedidos': todos_pedidos.count(),
        'enviado_cozinha': len(pedidos_por_status['Enviado_para_cozinha']),
        'em_preparo': len(pedidos_por_status['Em_preparo']),
        'pronto': len(pedidos_por_status['Pronto']),
        'saiu_entrega': len(pedidos_por_status['Saiu_para_entrega']),
        'retirada': len(pedidos_por_status['Retirada']),
        'balcao': len(pedidos_por_status['Balcao']),
    }
    
    # Status disponíveis baseados nos dados reais
    status_choices = [
        ('Enviado para cozinha', 'Enviado para Cozinha'),
        ('Em preparo', 'Em Preparo'),
        ('Pronto', 'Pronto'),
        ('Saiu para entrega', 'Saiu para Entrega'),
        ('Retirada', 'Retirada'),
        ('Balcão', 'Balcão'),
        ('Concluído', 'Concluído'),
        ('Cancelado', 'Cancelado'),
    ]
    
    # Mapeamento de próximos status para cada categoria
    proximos_status = {
        'Enviado para cozinha': ['Em preparo'],
        'Em preparo': ['Pronto'],
        'Pronto': ['Saiu para entrega', 'Retirada', 'Balcão'],
        'Saiu para entrega': ['Concluído'],
        'Retirada': ['Concluído'],
        'Balcão': ['Concluído'],
    }
    
    context = {
        'pedidos_por_status': pedidos_por_status,
        'stats': stats,
        'cliente_search': cliente_search,
        'telefone_search': telefone_search,
        'status_choices': status_choices,
        'proximos_status': proximos_status,
    }
    
    return render(request, 'admin/pedidos_fila.html', context)

@login_required
def atualizar_status_pedido(request, pedido_id):
    """
    View para atualizar o status de um pedido via AJAX
    Agora com notificações WhatsApp automáticas! 📱
    """
    if request.method == 'POST':
        try:
            novo_status = request.POST.get('status')
            
            # Usar a função global para atualizar status
            sucesso = atualizar_status_pedido_global(
                pedido_id=pedido_id,
                novo_status=novo_status,
                enviar_notificacao=True
            )
            
            if sucesso:
                return JsonResponse({
                    'success': True,
                    'message': f'Status atualizado para {novo_status}',
                    'novo_status': novo_status,
                    'notificacao_enviada': True
                })
            else:
                return JsonResponse({
                    'success': False,
                    'message': 'Erro ao atualizar status do pedido'
                }, status=400)
                
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f'Erro ao atualizar pedido: {str(e)}'
            }, status=500)
    
    return JsonResponse({
        'success': False,
        'message': 'Método não permitido'
    }, status=405)

@login_required
def criar_pedido_manual(request):
    """
    View para criar pedidos manualmente (telefone, presencial, etc.)
    """
    if request.method == 'POST':
        try:
            # Gerar ID único para o pedido
            import uuid
            id_pedido = str(uuid.uuid4())[:8]
            
            # Dados do cliente
            cliente_nome = request.POST.get('cliente_nome', '').strip()
            cliente_telefone = request.POST.get('cliente_telefone', '').strip()
            
            # Tipo de entrega
            tipo_entrega = request.POST.get('tipo_entrega', 'retirada')
            
            # Endereço (se entrega)
            endereco_entrega = {}
            if tipo_entrega == 'entrega':
                endereco_entrega = {
                    'endereco': request.POST.get('endereco', ''),
                    'distancia_km': float(request.POST.get('distancia_km', 0)),
                    'tempo_estimado': request.POST.get('tempo_estimado', '')
                }
            
            # Itens do pedido
            itens = []
            produtos = request.POST.getlist('produto[]')
            quantidades = request.POST.getlist('quantidade[]')
            valores = request.POST.getlist('valor[]')
            observacoes = request.POST.getlist('observacoes[]')
            
            valor_total = 0
            for i in range(len(produtos)):
                if produtos[i] and quantidades[i] and valores[i]:
                    item = {
                        'item_id': i + 1,
                        'produto': produtos[i],
                        'quantidade': int(quantidades[i]),
                        'valor_unitario': float(valores[i]),
                        'adicionais': [],
                        'observacoes': observacoes[i] if i < len(observacoes) else '',
                        'subtotal': int(quantidades[i]) * float(valores[i]),
                        'especificacao_parcial': False
                    }
                    itens.append(item)
                    valor_total += item['subtotal']
            
            # Valor da entrega
            valor_entrega = float(request.POST.get('valor_entrega', 0))
            valor_total_final = valor_total + valor_entrega
            
            # Forma de pagamento
            forma_pagamento = request.POST.get('forma_pagamento', 'dinheiro')
            
            # Criar pedido
            pedido = PedidoReal(
                id_pedido=id_pedido,
                cliente={
                    'nome': cliente_nome,
                    'telefone': cliente_telefone
                },
                itens=itens,
                instrucoes_cozinha={
                    'produto': ', '.join([item['produto'] for item in itens]),
                    'adicionais': [],
                    'observacoes': request.POST.get('observacoes_gerais', ''),
                    'especificacao_parcial': False
                },
                valor_total=valor_total,
                status='Enviado para cozinha',
                data_criacao=datetime.now().isoformat(),
                data_atualizacao=datetime.now().isoformat(),
                tipo_entrega=tipo_entrega,
                endereco_entrega=endereco_entrega,
                forma_pagamento=forma_pagamento,
                valor_entrega=valor_entrega,
                valor_total_final=valor_total_final,
                historico_status=[
                    {
                        'status': 'Enviado para cozinha',
                        'data': datetime.now().isoformat(),
                        'descricao': 'Pedido criado manualmente'
                    }
                ],
                estrutura_detalhada={
                    'total_itens': len(itens),
                    'resumo_cozinha': [f"Item {i+1}: {item['produto']}" for i, item in enumerate(itens)],
                    'resumo_caixa': [f"Item {i+1}: {item['produto']} = R$ {item['subtotal']:.2f}" for i, item in enumerate(itens)],
                    'resumo_entregador': [f"Item {i+1}: {item['produto']}" for i, item in enumerate(itens)]
                }
            )
            
            pedido.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Pedido #{id_pedido} criado com sucesso!',
                'pedido_id': str(pedido.id)
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f'Erro ao criar pedido: {str(e)}'
            }, status=500)
    
    # Buscar produtos disponíveis para o formulário
    produtos = Produto.objects.filter(disponivel=True).order_by('nome')
    
    return render(request, 'admin/criar_pedido_manual.html', {
        'produtos': produtos
    })


@login_required
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
