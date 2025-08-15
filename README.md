# Cardápio Online com Django + MongoDB

Este é um projeto de **Cardápio Online** desenvolvido em **Django** com integração ao **MongoDB**.  
O objetivo é permitir que restaurantes, lanchonetes ou similares possam gerenciar seu cardápio de forma simples e prática,  
mesmo sem acesso ao admin do Django, já que a integração com o MongoDB impede o uso direto da interface administrativa padrão.

---

## 📌 Funcionalidades
- Adicionar novos itens ao cardápio.
- Adicionar novos adicionais (extras).
- Upload de imagens para os produtos.
- Visualizar detalhes de cada produto.
- Organização dos itens por categorias.

---

## 🛠 Tecnologias Utilizadas
- **Python 3.x**
- **Django**
- **MongoDB** (com `djongo` ou `pymongo`, conforme configuração)
- **Bootstrap** (para o layout responsivo)
- **HTML5 / CSS3 / JavaScript**

---

## 📂 Estrutura de Mídia
O sistema utiliza o diretório **media/** para armazenar as imagens dos produtos.  
Por padrão:
```
media/
└── produtos/
    ├── produto1.jpg
    ├── produto2.jpg
```
Certifique-se de configurar corretamente no `settings.py`:
```python
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
```

E no `urls.py` principal:
```python
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # suas rotas
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

---

## 🚀 Como Executar o Projeto

1. **Clonar o repositório**
```bash
git clone https://github.com/seuusuario/seurepositorio.git
cd seurepositorio
```

2. **Criar e ativar um ambiente virtual**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate   # Windows
```

3. **Instalar as dependências**
```bash
pip install -r requirements.txt
```

4. **Configurar o banco de dados MongoDB**  
   - Ajuste as credenciais no `settings.py`.

5. **Rodar as migrações**
```bash
python manage.py migrate
```

6. **Iniciar o servidor**
```bash
python manage.py runserver
```

7. **Acessar no navegador**
```
http://127.0.0.1:8000/
```

---

## 📷 Exemplo de Tela
Tela inicial
![Início](docs/images/inicio.png)

Detalhes do produto
![Detalhes do produto](docs/images/detalhes.png)

Adicionar novos produtos
![Adicionar novos produtos](docs/images/adicionar_produto.png)
---

## 📄 Licença
Este projeto está sob a licença MIT
