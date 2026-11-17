# GoHub

Backend em Django e Django REST Framework, com processamento assíncrono via Celery e painel web baseado no tema DashLite.

## Sobre o projeto

O GoHub está em estágio inicial. Hoje ele é a base (o "esqueleto") sobre a qual os módulos de negócio serão construídos.

Já estão prontos:

- configuração do Django lida de variáveis de ambiente;
- API REST com documentação automática;
- fila de tarefas com Celery;
- execução em Docker para desenvolvimento e produção;
- layout HTML do painel.

Ainda não existem apps de domínio, models, endpoints de negócio, tarefas nem testes.

## Tecnologias utilizadas

- **Python 3.13** e **Django 5.2**
- **Django REST Framework**, **django-filter** e **drf-yasg** (documentação Swagger/ReDoc)
- **Jazzmin** (tema do Django Admin)
- **PostgreSQL** (banco de dados, via psycopg2)
- **Celery** com **Redis** como broker (fila de mensagens)
- **WhiteNoise** (arquivos estáticos) e **Gunicorn** (servidor em produção)
- **Docker** e **Docker Compose**
- **DashLite** (tema HTML/CSS/JS do painel, baseado em Bootstrap 5)

As variáveis do **MinIO** já existem nas configurações, mas ainda não há código que use o serviço.

## Estrutura do projeto

```
gohub/
├── setup/                  # Projeto Django
│   ├── settings.py         # Configurações (todas vindas de variáveis de ambiente)
│   ├── urls.py             # Rotas: admin, health, api, swagger, redoc
│   ├── celery.py           # Configuração do Celery
│   └── static/             # Arquivos do tema DashLite (CSS, JS, fontes, imagens)
├── app/                    # Destino das apps de domínio (app/<nome>)
├── templates/
│   ├── base.html           # Documento HTML base
│   ├── layouts/painel.html # Layout do painel; as páginas estendem este arquivo
│   ├── partials/           # Trechos reutilizáveis (cabeçalho, menu, rodapé, mensagens)
│   └── exemplos/           # Páginas de referência do tema (não são servidas)
├── Dockerfile              # Imagem da aplicação
├── docker-compose.yml      # Serviços web, worker e beat
├── docker-compose.override.yml  # Ajustes de desenvolvimento
├── docker-compose.prod.yml # Ajustes de produção
├── entrypoint.sh           # Aguarda dependências e inicia o processo
├── gunicorn.conf.py        # Configuração do Gunicorn
├── requirements.txt        # Dependências com versões fixas
└── .env.example            # Modelo das variáveis de ambiente
```

As pastas `static/` (gerada pelo `collectstatic`) e `media/` (uploads) são criadas automaticamente e não devem ser editadas.

## Como executar o projeto

### Pré-requisitos

- Python 3.13
- PostgreSQL e Redis acessíveis
- Docker e Docker Compose (apenas para execução em containers)

### Configuração

Copie o modelo de variáveis de ambiente e preencha os valores:

```bash
cp .env.example .env
```

A `SECRET_KEY` é obrigatória: sem ela, nenhum comando do Django funciona. Para gerar uma:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Mantenha `127.0.0.1` em `DJANGO_ALLOWED_HOSTS`, pois o healthcheck do container usa esse endereço.

Nunca versione o arquivo `.env`.

### Opção 1: ambiente local (venv)

Os hosts do `.env.example` apontam para `localhost`, então Postgres e Redis precisam estar acessíveis na sua máquina.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Para rodar o worker do Celery em outro terminal:

```bash
celery -A setup worker --loglevel=INFO
```

### Opção 2: Docker (desenvolvimento)

Postgres, Redis e MinIO **não** fazem parte deste Compose. Eles rodam em stacks próprias, conectadas pela rede externa `network_public`, que precisa existir antes:

```bash
docker network create network_public
```

Suba a aplicação:

```bash
docker compose up
```

Em desenvolvimento, o código é montado no container e o `runserver` recarrega sozinho. O worker e o beat não recarregam: após alterar tarefas, rode:

```bash
docker compose restart worker beat
```

O entrypoint **não** aplica migrations. Rode comandos pontuais assim:

```bash
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py createsuperuser
```

### Opção 3: Docker (produção)

Usa a imagem publicada e o Gunicorn. Defina `GOHUB_IMAGE` e `GOHUB_TAG` no `.env` do servidor e execute:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Mantenha apenas uma instância do serviço `beat`.

### Acessando a aplicação

Com o servidor rodando, acesse em `http://127.0.0.1:8000`:

| Rota | Descrição |
|------|-----------|
| `/admin/` | Django Admin (tema Jazzmin) |
| `/health/` | Verificação de saúde (retorna JSON) |
| `/api/` | Raiz da API REST |
| `/swagger/` | Documentação interativa da API |
| `/redoc/` | Documentação da API em formato ReDoc |

## Funcionalidades principais

- Django Admin com o tema Jazzmin.
- Endpoint `/health/`, usado pelo healthcheck do Docker.
- API REST com paginação (20 itens por página), autenticação por sessão ou Basic Auth e permissões por model (`DjangoModelPermissions`).
- Documentação automática da API em Swagger e ReDoc.
- Celery configurado para descobrir automaticamente os `tasks.py` das apps, com serviços de worker e beat.
- Layout do painel (`templates/layouts/painel.html`) pronto para as futuras páginas.
- Idioma `pt-BR` e fuso horário `America/Sao_Paulo`.

## Considerações finais

O GoHub é uma base pronta para receber os módulos de negócio, que devem ser criados como apps em `app/<nome>`.

Pontos importantes para manutenção:

- Toda nova configuração deve vir de variável de ambiente e também ser adicionada ao `.env.example`.
- Migrations são aplicadas manualmente, nunca pelo entrypoint.
- A imagem Docker instala apenas pacotes com wheel pré-compilado; uma nova dependência precisa ter wheel disponível.
- Com `DEBUG` desligado, referenciar um arquivo estático inexistente com `{% static %}` causa erro 500.
- Use `django.utils.timezone.now()` para datas, e não `datetime.now()`.
