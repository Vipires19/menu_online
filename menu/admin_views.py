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
    """
    if request.method == 'POST':
        try:
            pedido = PedidoReal.objects.get(id=pedido_id)
            novo_status = request.POST.get('status')
            
            # Status válidos
            status_validos = [
                'Enviado para cozinha', 'Em preparo', 'Pronto', 
                'Saiu para entrega', 'Retirada', 'Balcão', 'Concluído', 'Cancelado'
            ]
            
            if novo_status in status_validos:
                # Adicionar ao histórico de status
                historico_entry = {
                    'status': novo_status,
                    'data': datetime.now().isoformat(),
                    'descricao': f'Status alterado para: {novo_status}'
                }
                
                if not pedido.historico_status:
                    pedido.historico_status = []
                pedido.historico_status.append(historico_entry)
                
                # Atualizar status e data
                pedido.status = novo_status
                pedido.data_atualizacao = datetime.now().isoformat()
                
                pedido.save()
                
                return JsonResponse({
                    'success': True,
                    'message': f'Status atualizado para {novo_status}',
                    'novo_status': novo_status
                })
            else:
                return JsonResponse({
                    'success': False,
                    'message': 'Status inválido'
                }, status=400)
                
        except PedidoReal.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Pedido não encontrado'
            }, status=404)
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

