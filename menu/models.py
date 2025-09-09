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

class PedidoReal(Document):
    """
    Modelo que corresponde à estrutura real dos pedidos no MongoDB
    """
    # ID do pedido personalizado
    id_pedido = fields.StringField(max_length=50)
    
    # Informações do cliente (documento embutido)
    cliente = fields.DictField()
    
    # Itens do pedido
    itens = fields.ListField(fields.DictField())
    
    # Instruções para cozinha
    instrucoes_cozinha = fields.DictField()
    
    # Valores
    valor_total = fields.FloatField()
    subtotal = fields.FloatField()
    
    # Status do pedido
    status = fields.StringField()
    
    # Datas
    data_criacao = fields.StringField()  # String ISO format
    data_atualizacao = fields.StringField()  # String ISO format
    
    # Tipo de entrega
    tipo_entrega = fields.StringField()  # "entrega" ou "retirada"
    
    # Endereço de entrega (quando aplicável)
    endereco_entrega = fields.DictField()
    
    # Forma de pagamento
    forma_pagamento = fields.StringField()
    
    # Valor da entrega
    valor_entrega = fields.FloatField()
    valor_total_final = fields.FloatField()
    
    # Histórico de status
    historico_status = fields.ListField(fields.DictField())
    
    # Estrutura detalhada
    estrutura_detalhada = fields.DictField()
    
    # Cobrança
    cobranca_id = fields.StringField()
    link_pagamento = fields.StringField()
    
    # Campos para pagamento em dinheiro
    status_pagamento = fields.StringField()  # "pendente", "pago", "cancelado"
    valor_recebido = fields.FloatField()  # Valor que o cliente entregou
    troco = fields.FloatField()  # Troco a ser devolvido
    
    meta = {
        'collection': 'pedidos',  # Usar a mesma coleção
        'ordering': ['-data_criacao'],
        'indexes': [
            'status',
            'data_criacao',
            'id_pedido',
            'cliente.telefone',
        ]
    }
    
    def __str__(self):
        cliente_nome = self.cliente.get('nome', 'Cliente') if self.cliente else 'Cliente'
        return f"Pedido #{self.id_pedido} - {cliente_nome}"
    
    @property
    def cliente_nome(self):
        """Propriedade para acessar o nome do cliente"""
        return self.cliente.get('nome', '') if self.cliente else ''
    
    @property
    def cliente_telefone(self):
        """Propriedade para acessar o telefone do cliente"""
        return self.cliente.get('telefone', '') if self.cliente else ''
    
    @property
    def endereco_completo(self):
        """Propriedade para acessar o endereço completo"""
        if self.endereco_entrega and 'endereco' in self.endereco_entrega:
            return self.endereco_entrega['endereco']
        return None
    
    @property
    def data_pedido_formatada(self):
        """Converte string ISO para datetime para exibição"""
        try:
            from datetime import datetime
            return datetime.fromisoformat(self.data_criacao.replace('Z', '+00:00'))
        except:
            return datetime.now()
    
    @property
    def status_traduzido(self):
        """Traduz o status para português"""
        traducoes = {
            'Aguardando definição de entrega': 'Aguardando Entrega',
            'Aguardando forma de pagamento': 'Aguardando Pagamento',
            'Aguardando pagamento': 'Aguardando Pagamento',
            'Enviado para cozinha': 'Preparando',
            'Em preparo': 'Preparando',
            'Pronto': 'Pronto',
            'Saiu para entrega': 'Saiu Entrega',
            'Entregue': 'Entregue',
            'Cancelado': 'Cancelado'
        }
        return traducoes.get(self.status, self.status)
    
    @property
    def status_cor(self):
        """Retorna a classe CSS para a cor do status"""
        status_lower = self.status.lower()
        if 'cozinha' in status_lower or 'preparo' in status_lower:
            return 'bg-info text-white'
        elif 'aguardando' in status_lower:
            return 'bg-warning text-dark'
        elif 'saiu' in status_lower or 'entrega' in status_lower:
            return 'bg-success text-white'
        elif 'entregue' in status_lower or 'pronto' in status_lower:
            return 'bg-primary text-white'
        elif 'cancelado' in status_lower:
            return 'bg-danger text-white'
        else:
            return 'bg-secondary text-white'
    
    @property
    def troco_calculado(self):
        """Calcula o troco quando pagamento é em dinheiro"""
        if self.forma_pagamento == 'dinheiro' and self.valor_recebido and self.valor_total_final:
            return max(0, self.valor_recebido - self.valor_total_final)
        return 0
    
    @property
    def status_pagamento_traduzido(self):
        """Traduz o status de pagamento para português"""
        traducoes = {
            'pendente': 'Pendente',
            'pago': 'Pago',
            'cancelado': 'Cancelado',
            'aguardando': 'Aguardando Pagamento'
        }
        return traducoes.get(self.status_pagamento, self.status_pagamento or 'Não informado')
    
    @property
    def precisa_troco(self):
        """Verifica se precisa de troco"""
        return (self.forma_pagamento == 'dinheiro' and 
                self.valor_recebido and 
                self.valor_recebido > self.valor_total_final)