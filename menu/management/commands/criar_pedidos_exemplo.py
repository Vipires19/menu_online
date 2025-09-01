from django.core.management.base import BaseCommand
from menu.models import Pedido
from datetime import datetime, timedelta
import random

class Command(BaseCommand):
    help = 'Cria pedidos de exemplo para testar a fila de preparo'

    def handle(self, *args, **options):
        # Dados de exemplo para pedidos
        pedidos_exemplo = [
            {
                'cliente_nome': 'João Silva',
                'cliente_telefone': '(11) 99999-1111',
                'cliente_email': 'joao@email.com',
                'endereco': {
                    'rua': 'Rua das Flores',
                    'numero': '123',
                    'complemento': 'Apto 45',
                    'bairro': 'Centro',
                    'cidade': 'São Paulo',
                    'cep': '01234-567'
                },
                'itens': [
                    {
                        'nome': 'X-Burger',
                        'quantidade': 2,
                        'preco_unitario': 15.90,
                        'adicionais': ['Bacon extra', 'Queijo cheddar'],
                        'observacoes': 'Sem cebola'
                    },
                    {
                        'nome': 'Batata Frita',
                        'quantidade': 1,
                        'preco_unitario': 8.50,
                        'adicionais': ['Molho especial'],
                        'observacoes': ''
                    }
                ],
                'subtotal': 40.30,
                'taxa_entrega': 5.00,
                'total': 45.30,
                'status': 'confirmado',
                'observacoes': 'Entregar no portão',
                'data_pedido': datetime.now() - timedelta(minutes=30)
            },
            {
                'cliente_nome': 'Maria Santos',
                'cliente_telefone': '(11) 88888-2222',
                'cliente_email': 'maria@email.com',
                'endereco': {
                    'rua': 'Av. Paulista',
                    'numero': '1000',
                    'complemento': 'Sala 200',
                    'bairro': 'Bela Vista',
                    'cidade': 'São Paulo',
                    'cep': '01310-100'
                },
                'itens': [
                    {
                        'nome': 'Pizza Margherita',
                        'quantidade': 1,
                        'preco_unitario': 25.00,
                        'adicionais': ['Borda recheada'],
                        'observacoes': ''
                    }
                ],
                'subtotal': 25.00,
                'taxa_entrega': 3.50,
                'total': 28.50,
                'status': 'preparando',
                'observacoes': '',
                'data_pedido': datetime.now() - timedelta(minutes=15)
            },
            {
                'cliente_nome': 'Pedro Costa',
                'cliente_telefone': '(11) 77777-3333',
                'cliente_email': 'pedro@email.com',
                'endereco': None,  # Retirada
                'itens': [
                    {
                        'nome': 'Combo Lanche + Refrigerante',
                        'quantidade': 1,
                        'preco_unitario': 22.90,
                        'adicionais': [],
                        'observacoes': ''
                    },
                    {
                        'nome': 'Milk Shake',
                        'quantidade': 1,
                        'preco_unitario': 12.00,
                        'adicionais': ['Chocolate extra'],
                        'observacoes': ''
                    }
                ],
                'subtotal': 34.90,
                'taxa_entrega': 0.00,
                'total': 34.90,
                'status': 'saiu_entrega',
                'observacoes': 'Retirada no balcão',
                'data_pedido': datetime.now() - timedelta(minutes=45)
            },
            {
                'cliente_nome': 'Ana Oliveira',
                'cliente_telefone': '(11) 66666-4444',
                'cliente_email': 'ana@email.com',
                'endereco': {
                    'rua': 'Rua Augusta',
                    'numero': '500',
                    'complemento': '',
                    'bairro': 'Consolação',
                    'cidade': 'São Paulo',
                    'cep': '01212-000'
                },
                'itens': [
                    {
                        'nome': 'Salada Caesar',
                        'quantidade': 1,
                        'preco_unitario': 18.50,
                        'adicionais': ['Frango grelhado'],
                        'observacoes': 'Sem croutons'
                    },
                    {
                        'nome': 'Suco Natural',
                        'quantidade': 2,
                        'preco_unitario': 6.00,
                        'adicionais': [],
                        'observacoes': 'Um de laranja, um de limão'
                    }
                ],
                'subtotal': 30.50,
                'taxa_entrega': 4.00,
                'total': 34.50,
                'status': 'confirmado',
                'observacoes': 'Entregar no condomínio',
                'data_pedido': datetime.now() - timedelta(minutes=5)
            }
        ]

        # Criar os pedidos
        for dados_pedido in pedidos_exemplo:
            pedido = Pedido(**dados_pedido)
            pedido.save()
            self.stdout.write(
                self.style.SUCCESS(f'Pedido criado: {pedido.cliente_nome} - {pedido.status}')
            )

        self.stdout.write(
            self.style.SUCCESS(f'Total de {len(pedidos_exemplo)} pedidos criados com sucesso!')
        )


