from django.db import models
from mongoengine import Document, EmbeddedDocument, fields
from django.urls import reverse
from datetime import datetime
import os
from django.core.files.storage import default_storage
from django.conf import settings

# Create your models here.

class Adicional(EmbeddedDocument):
    """
    Documento embutido que representa um adicional do produto
    No MongoDB, podemos embutir documentos dentro de outros documentos
    Isso é mais eficiente que relacionamentos em bancos relacionais
    """
    nome = fields.StringField(max_length=100, required=True, verbose_name="Nome do Adicional")
    preco = fields.DecimalField(min_value=0, precision=2, required=True, verbose_name="Preço do Adicional")
    disponivel = fields.BooleanField(default=True, verbose_name="Disponível")
    
    def __str__(self):
        return f"{self.nome} - R$ {self.preco}"

class Produto(Document):
    """
    Documento principal que representa um produto do cardápio
    No MongoDB, cada produto é um documento JSON flexível
    """
    # Campos básicos do produto
    nome = fields.StringField(max_length=200, required=True, verbose_name="Nome do Produto")
    descricao = fields.StringField(verbose_name="Descrição")
    preco = fields.DecimalField(min_value=0, precision=2, required=True, verbose_name="Preço")
    categoria = fields.StringField(max_length=100, required=True, verbose_name="Categoria")
    
    # Campo para imagem (armazena o caminho do arquivo)
    imagem = fields.StringField(verbose_name="Caminho da Imagem")
    
    # Status e controle
    disponivel = fields.BooleanField(default=True, verbose_name="Disponível")
    destaque = fields.BooleanField(default=False, verbose_name="Produto em Destaque")
    
    # Adicionais embutidos (vantagem do MongoDB)
    adicionais = fields.ListField(fields.EmbeddedDocumentField(Adicional), verbose_name="Adicionais")
    
    # Campos de auditoria
    data_criacao = fields.DateTimeField(default=datetime.now, verbose_name="Data de Criação")
    data_atualizacao = fields.DateTimeField(default=datetime.now, verbose_name="Última Atualização")
    
    # Campos extras que podem ser úteis (flexibilidade do MongoDB)
    tags = fields.ListField(fields.StringField(max_length=50), verbose_name="Tags")
    ingredientes = fields.ListField(fields.StringField(max_length=100), verbose_name="Ingredientes")
    informacoes_nutricionais = fields.DictField(verbose_name="Informações Nutricionais")
    tempo_preparo = fields.IntField(min_value=0, verbose_name="Tempo de Preparo (minutos)")
    
    # Configurações do documento
    meta = {
        'collection': 'produtos',  # Nome da coleção no MongoDB
        'ordering': ['categoria', 'nome'],  # Ordenação padrão
        'indexes': [
            'nome',  # Índice para busca por nome
            'categoria',  # Índice para filtro por categoria
            'disponivel',  # Índice para produtos disponíveis
            ('categoria', 'nome'),  # Índice composto
        ]
    }
    
    def __str__(self):
        return self.nome
    
    def get_absolute_url(self):
        """Retorna a URL para a página de detalhes do produto"""
        return reverse('produto_detalhe', kwargs={'produto_id': str(self.id)})
    
    def save(self, *args, **kwargs):
        """
        Sobrescreve o método save para atualizar a data de modificação
        """
        self.data_atualizacao = datetime.now()
        return super().save(*args, **kwargs)
    
    @classmethod
    def get_categorias(cls):
        """
        Método de classe para buscar todas as categorias disponíveis
        Usa agregação do MongoDB para performance
        """
        pipeline = [
            {"$match": {"disponivel": True}},  # Apenas produtos disponíveis
            {"$group": {"_id": "$categoria"}},  # Agrupa por categoria
            {"$sort": {"_id": 1}}  # Ordena alfabeticamente
        ]
        
        result = cls.objects.aggregate(pipeline)
        return [item['_id'] for item in result]
    
    @classmethod
    def buscar_produtos(cls, termo_busca=None, categoria=None, apenas_disponiveis=True):
        """
        Método para busca avançada de produtos
        Aproveita as capacidades de busca do MongoDB
        """
        query = {}
        
        if apenas_disponiveis:
            query['disponivel'] = True
            
        if categoria:
            query['categoria'] = categoria
            
        if termo_busca:
            # Busca por texto em nome, descrição e tags
            query['$or'] = [
                {'nome': {'$regex': termo_busca, '$options': 'i'}},
                {'descricao': {'$regex': termo_busca, '$options': 'i'}},
                {'tags': {'$in': [termo_busca]}}
            ]
        
        return cls.objects(__raw__=query)
    
    @property
    def imagem_url(self):
        """
        Retorna URL pública da imagem:
         - se não houver imagem: placeholder em static
         - tenta default_storage.url() (funciona para local e S3)
         - fallback para MEDIA_URL + caminho salvo
        """
        if not self.imagem:
            return settings.STATIC_URL + 'img/placeholder.png'
        try:
            return default_storage.url(self.imagem)
        except Exception:
            return settings.MEDIA_URL + str(self.imagem)

class Pedido(Document):
    """
    Documento para armazenar pedidos dos clientes
    Demonstra a flexibilidade do MongoDB para estruturas complexas
    """
    # Informações do cliente
    cliente_nome = fields.StringField(max_length=200, required=True)
    cliente_telefone = fields.StringField(max_length=20)
    cliente_email = fields.EmailField()
    
    # Endereço de entrega (documento embutido)
    endereco = fields.DictField()
    
    # Itens do pedido (lista de documentos embutidos)
    itens = fields.ListField(fields.DictField())
    
    # Valores
    subtotal = fields.DecimalField(min_value=0, precision=2)
    taxa_entrega = fields.DecimalField(min_value=0, precision=2, default=0)
    total = fields.DecimalField(min_value=0, precision=2)
    
    # Status do pedido
    STATUS_CHOICES = [
        ('pendente', 'Pendente'),
        ('confirmado', 'Confirmado'),
        ('preparando', 'Preparando'),
        ('saiu_entrega', 'Saiu para Entrega'),
        ('entregue', 'Entregue'),
        ('cancelado', 'Cancelado'),
    ]
    status = fields.StringField(choices=STATUS_CHOICES, default='pendente')
    
    # Datas
    data_pedido = fields.DateTimeField(default=datetime.now)
    data_entrega_prevista = fields.DateTimeField()
    data_entrega_realizada = fields.DateTimeField()
    
    # Observações
    observacoes = fields.StringField()
    
    meta = {
        'collection': 'pedidos',
        'ordering': ['-data_pedido'],
        'indexes': [
            'status',
            'data_pedido',
            'cliente_telefone',
        ]
    }
    
    def __str__(self):
        return f"Pedido #{self.id} - {self.cliente_nome}"