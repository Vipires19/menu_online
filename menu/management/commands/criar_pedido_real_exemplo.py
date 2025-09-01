from django.core.management.base import BaseCommand
from menu.models import PedidoReal
from datetime import datetime, timedelta

class Command(BaseCommand):
    help = 'Cria um pedido de exemplo no formato real do MongoDB'

    def handle(self, *args, **options):
        # Dados de exemplo no formato real
        pedido_exemplo = {
            'id_pedido': '737e0d9a',
            'cliente': {
                'nome': 'Vinícius Pires',
                'telefone': '16981394877'
            },
            'itens': [
                {
                    'item_id': 1,
                    'produto': 'Pirão Burger',
                    'produto_id': '68a093321dec23d94b5091a7',
                    'quantidade': 1,
                    'valor_unitario': 50.0,
                    'adicionais': [],
                    'observacoes': '',
                    'subtotal': 50.0,
                    'especificacao_parcial': False
                }
            ],
            'instrucoes_cozinha': {
                'produto': 'Pirão Burger',
                'adicionais': [],
                'observacoes': '',
                'especificacao_parcial': False
            },
            'valor_total': 50.0,
            'status': 'Enviado para cozinha',
            'data_criacao': datetime.now().isoformat(),
            'data_atualizacao': datetime.now().isoformat(),
            'tipo_entrega': 'entrega',
            'endereco_entrega': {
                'endereco': 'Av. Paschoal Innechi, 1538',
                'distancia_km': 6.945,
                'tempo_estimado': '14 minutos'
            },
            'forma_pagamento': 'pix',
            'valor_entrega': 13.42,
            'valor_total_final': 63.42,
            'historico_status': [
                {
                    'status': 'Aguardando definição de entrega',
                    'data': (datetime.now() - timedelta(minutes=10)).isoformat(),
                    'descricao': 'Pedido criado e aguardando definição de entrega/retirada'
                },
                {
                    'status': 'Enviado para cozinha',
                    'data': datetime.now().isoformat(),
                    'descricao': 'Status alterado para: Enviado para cozinha'
                }
            ],
            'estrutura_detalhada': {
                'total_itens': 1,
                'resumo_cozinha': ['Item 1: Pirão Burger'],
                'resumo_caixa': ['Item 1: Pirão Burger = R$ 50.00'],
                'resumo_entregador': ['Item 1: Pirão Burger']
            },
            'cobranca_id': 'pay_awhlbbzst16gwejk',
            'link_pagamento': 'https://sandbox.asaas.com/i/awhlbbzst16gwejk'
        }

        # Criar mais alguns pedidos com status diferentes
        pedidos_exemplo = [
            pedido_exemplo,
            {
                **pedido_exemplo,
                'id_pedido': '123abc45',
                'cliente': {'nome': 'Maria Silva', 'telefone': '11987654321'},
                'status': 'Em preparo',
                'data_criacao': (datetime.now() - timedelta(minutes=20)).isoformat(),
                'itens': [
                    {
                        'item_id': 2,
                        'produto': 'Pizza Margherita',
                        'quantidade': 1,
                        'valor_unitario': 35.0,
                        'adicionais': [{'nome': 'Borda recheada', 'preco': 5.0}],
                        'observacoes': 'Massa fina',
                        'subtotal': 40.0
                    }
                ],
                'valor_total': 40.0,
                'valor_total_final': 45.0,
                'valor_entrega': 5.0
            },
            {
                **pedido_exemplo,
                'id_pedido': 'xyz789ef',
                'cliente': {'nome': 'João Santos', 'telefone': '21999888777'},
                'status': 'Pronto',
                'tipo_entrega': 'retirada',
                'endereco_entrega': {},
                'valor_entrega': 0.0,
                'valor_total_final': 50.0,
                'data_criacao': (datetime.now() - timedelta(minutes=5)).isoformat(),
            }
        ]

        # Criar os pedidos
        for i, dados_pedido in enumerate(pedidos_exemplo):
            try:
                pedido = PedidoReal(**dados_pedido)
                pedido.save()
                self.stdout.write(
                    self.style.SUCCESS(f'Pedido {i+1} criado: {pedido.cliente_nome} - {pedido.status}')
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'Erro ao criar pedido {i+1}: {str(e)}')
                )

        self.stdout.write(
            self.style.SUCCESS(f'Total de {len(pedidos_exemplo)} pedidos criados com sucesso!')
        )
