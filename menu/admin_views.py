# menu/admin_views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required as staff_required
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from .forms import ProdutoForm, AdicionalForm
from .models import Produto, Adicional

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

@staff_required
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

@staff_required
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

@staff_required
def produto_admin_delete(request, pk):
    produto = get_object_or_404(Produto, id=pk)
    produto.delete()
    return redirect('produtos_admin_list')
