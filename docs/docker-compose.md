# Docker Compose e Dockerfile

Esta é a página de referência técnica do stack: cada arquivo de infraestrutura do
repositório é reproduzido aqui **exatamente como está no disco** e comentado linha a
linha, com a explicação do *porquê* de cada escolha — não só do *o quê*.

Os quatro blocos abaixo cobrem, nesta ordem:

| Parte | Arquivo | O que ele resolve |
| --- | --- | --- |
| 1 | `docker-compose.yml` | Orquestração: três serviços, uma rede, três volumes nomeados |
| 2 | `Dockerfile` | Imagem da aplicação em três estágios (`builder`, `runtime`, `test`) |
| 3 | `entrypoint.sh` | O que acontece entre o `docker start` e o primeiro request atendido |
| 4 | `nginx/default.conf` + `nginx/Dockerfile` | Proxy reverso, entrega de `/media/` e `/static/` e o endurecimento de segurança |

Se você ainda não viu o desenho geral do stack, comece por
[Arquitetura](arquitetura.md); para subir tudo pela primeira vez, veja
[Instalação e execução](instalacao.md).

!!! note "Um princípio atravessa o arquivo inteiro"
    Somente o Nginx publica porta no host. Gunicorn e PostgreSQL existem apenas dentro
    da rede `backend` do Compose. Tudo que é estado (banco, uploads, estáticos) vive em
    **volume nomeado**, nunca na camada gravável do contêiner — é isso que faz os
    uploads sobreviverem a `docker compose down` seguido de `up`.

---

## Parte 1 — `docker-compose.yml`

### Cabeçalho do arquivo

```yaml
# Stack: Nginx (reverse proxy, the only published port) -> Gunicorn/Django -> PostgreSQL.
# Named volumes keep uploads, static files and database data across `down` + `up`.
name: django-upload-stack
```

A chave `name:` no topo define o **nome do projeto Compose**. Sem ela, o Compose usa o
nome do diretório em que o `docker-compose.yml` está — o que, neste repositório clonado
dentro de `OneDrive/Desktop`, poderia virar qualquer coisa dependendo de como a pasta foi
nomeada na máquina de quem clonou.

Isso importa muito mais do que parece, porque o nome do projeto é o **prefixo de todos os
recursos criados**: os volumes reais se chamam `django-upload-stack_media_data`,
`django-upload-stack_static_data` e `django-upload-stack_postgres_data`, e a rede se chama
`django-upload-stack_backend`. Fixar `name:` garante que:

- renomear ou mover a pasta do projeto **não** cria um segundo conjunto de volumes vazios
  (sintoma clássico: "meus uploads sumiram" quando na verdade o stack subiu apontando para
  volumes novos);
- os comandos `docker volume ls | grep django-upload-stack` sempre encontram os mesmos
  nomes, em qualquer máquina.

### Serviço `db` — PostgreSQL

```yaml
  db:
    image: postgres:17-alpine
    container_name: dus-db
    restart: unless-stopped
    # Only the database credentials, never the application's SECRET_KEY.
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      # postgres:17 stores data in /var/lib/postgresql/data.
      # For postgres:18+ this mount must become /var/lib/postgresql instead.
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      # $$ is escaped so the variable is expanded inside the container, not by Compose.
      # Checking over TCP avoids the false positive from initdb's temporary unix-socket
      # server during the very first boot.
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 10s
    networks: [backend]
```

#### `image` e `container_name`

`postgres:17-alpine` é uma tag **fixa de major version**. Não é `postgres:latest`: uma
atualização silenciosa de major do PostgreSQL faz o servidor recusar subir sobre um
diretório de dados criado por uma versão anterior (`database files are incompatible with
server`), e o volume nomeado garante que o diretório antigo continua lá. Fixar o major é o
que impede esse acidente.

`container_name: dus-db` dá um nome previsível (`docker logs dus-db` funciona sem
descobrir o hash). O preço é real e vale registrar: **um serviço com `container_name` não
pode ser escalado** (`docker compose up --scale db=2` falha, porque dois contêineres não
podem ter o mesmo nome). Para este stack didático, com exatamente uma réplica por serviço,
a previsibilidade compensa.

#### `environment` e não `env_file` — o ponto mais importante do serviço

O `db` recebe **apenas** `POSTGRES_DB`, `POSTGRES_USER` e `POSTGRES_PASSWORD`, listados um
a um. Ele não usa `env_file: .env` como o `web`. A diferença não é estética:

- `env_file: .env` injeta **todo** o arquivo dentro do contêiner. O `.env` contém
  `SECRET_KEY`, que é a chave de assinatura de sessões, tokens CSRF e links assinados do
  Django. Um contêiner de banco não tem absolutamente nenhum uso para ela.
- Qualquer coisa em `environment` é visível via `docker inspect dus-db`, em
  `/proc/1/environ` dentro do contêiner e nos logs de crash. Se o processo do PostgreSQL
  for comprometido, o atacante ganha o banco; não há motivo para entregar de brinde a
  capacidade de **forjar sessões autenticadas** da aplicação.

É o princípio do menor privilégio aplicado a variáveis de ambiente: cada serviço recebe o
mínimo de que precisa para funcionar. A lista completa de variáveis e quem consome cada
uma está em [Variáveis de ambiente](variaveis.md).

!!! warning "As três variáveis não têm valor padrão"
    Repare que é `${POSTGRES_DB}` e não `${POSTGRES_DB:-algo}`. Se o `.env` não existir ou
    não definir as três, o Compose interpola string vazia, o `initdb` falha e o `db` nunca
    fica saudável. Isso é proposital: é melhor quebrar alto e cedo do que subir um banco
    com usuário/senha adivinháveis. Copie `.env.example` para `.env` antes do primeiro
    `up`.

#### `volumes` e a nota sobre o PostgreSQL 18

```yaml
      - postgres_data:/var/lib/postgresql/data
```

Esse é o `PGDATA` da imagem oficial na série 17. O comentário no arquivo existe porque o
caminho **mudou a partir da imagem `postgres:18`**: lá o diretório de dados passou a ser
`/var/lib/postgresql` (o `data` virou um subdiretório interno). Consequência prática: se
alguém trocar a tag para `postgres:18-alpine` e **não** ajustar o ponto de montagem, o
contêiner sobe, o banco funciona... e os dados vão para a camada gravável do contêiner, em
vez do volume. O `down`+`up` seguinte apaga tudo — exatamente a falha que este projeto
existe para demonstrar que não acontece. Por isso o aviso está inline no YAML, e não só
aqui.

#### `healthcheck` — os dois detalhes que costumam passar despercebidos

```yaml
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
```

**Por que `$$` e não `$`.** O Compose faz interpolação de variáveis no próprio arquivo
YAML antes de entregá-lo ao Docker. Se estivesse escrito `${POSTGRES_USER}`, o **Compose**
substituiria o valor na hora de gerar a configuração, e o healthcheck gravado na
especificação do contêiner ficaria com a credencial *hard-coded*. Escrevendo `$$`, o
Compose "desescapa" para um `$` literal, e quem enxerga `${POSTGRES_USER}` é o shell
**dentro** do contêiner — que resolve a variável a partir do `environment` do serviço.
Resultado: o comando funciona mesmo que você mude o usuário no `.env`, e o valor não
aparece duplicado na saída de `docker compose config`.

Para ver a diferença com os próprios olhos:

```bash
docker compose config
```

O `test` aparece com `${POSTGRES_USER}` preservado, não expandido.

**Por que `-h 127.0.0.1` em vez do socket Unix.** Esse é o detalhe que evita uma corrida
sutil no **primeiro** boot. O entrypoint da imagem oficial do PostgreSQL, quando o volume
está vazio, roda `initdb` e depois sobe um **servidor temporário** para aplicar os scripts
de inicialização e criar o banco/usuário. Esse servidor temporário escuta **somente no
socket Unix** — justamente para que ninguém de fora consiga conectar enquanto o banco
ainda está sendo preparado.

Se o `pg_isready` fosse pelo socket (o padrão), ele responderia *"accepting connections"*
durante essa janela. O Compose marcaria o `db` como `healthy`, liberaria o `web`
(`condition: service_healthy`), e o `entrypoint.sh` tentaria rodar `migrate` contra um
banco que ainda vai ser reiniciado — falha intermitente, difícil de reproduzir, que só
aparece no primeiro `up` de uma máquina limpa ou logo depois de um `down -v`.

Checando por TCP em `127.0.0.1`, o healthcheck só passa quando o servidor **definitivo**
está ouvindo na porta 5432 — que é exatamente a condição que o `web` precisa.

Os tempos: `interval: 5s` com `retries: 10` e `start_period: 10s`. Durante o
`start_period`, falhas **não** contam para o limite de tentativas e não marcam o contêiner
como `unhealthy` — é a janela de cortesia para o `initdb` terminar. Depois dela, dez
falhas seguidas (≈50 s) marcam o serviço como doente.

#### `restart: unless-stopped`

Reinicia o contêiner se o processo morrer e o traz de volta quando o Docker Desktop ou a
máquina reinicia — mas **não** o ressuscita se você o parou de propósito com
`docker compose stop`. É a diferença em relação a `restart: always`, que reiniciaria até
algo que você desligou intencionalmente e transforma depuração em briga com o orquestrador.

### Serviço `web` — Gunicorn + Django

```yaml
  web:
    build:
      context: .
      target: runtime
    image: ${WEB_IMAGE:-django-upload-stack-web:local}
    container_name: dus-web
    restart: unless-stopped
    env_file: .env
    environment:
      # Always talk to the db service, whatever the .env says (it is also used by CI,
      # where POSTGRES_HOST points at localhost).
      POSTGRES_HOST: db
      POSTGRES_PORT: 5432
    volumes:
      - media_data:/app/media
      - static_data:/app/staticfiles
    # No "ports": Gunicorn is only reachable through Nginx on the internal network.
    expose:
      - "8000"
    depends_on:
      db:
        condition: service_healthy
    healthcheck:
      # An explicit Host header keeps the probe valid no matter how ALLOWED_HOSTS is set.
      test:
        - CMD
        - python
        - -c
        - |
          import sys, urllib.request
          req = urllib.request.Request(
              "http://127.0.0.1:8000/healthz/", headers={"Host": "localhost"}
          )
          sys.exit(0 if urllib.request.urlopen(req, timeout=5).status == 200 else 1)
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s
      start_interval: 2s
    networks: [backend]
```

#### `build` com `target` + `image`

`build.context: .` usa a raiz do repositório como contexto e `target: runtime` para
**no segundo estágio** do Dockerfile, sem construir o estágio `test`. O `.dockerignore`
mantém esse contexto enxuto: ele exclui `.git`, `.github`, `docs`, `site`, `mkdocs.yml`,
`nginx`, `scripts`, `docker-compose*.yml`, `*.md`, `.venv`, caches, `media/`,
`staticfiles/` e — criticamente — `.env` e `.env.*`. Ou seja, **o segredo nunca entra na
imagem**, nem como camada intermediária; ele chega em tempo de execução via `env_file`.

`image: ${WEB_IMAGE:-django-upload-stack-web:local}` faz duas coisas ao mesmo tempo:

- dá um nome estável à imagem construída localmente (`django-upload-stack-web:local`);
- permite substituir a imagem inteira por uma já publicada, sem editar o YAML. Definindo
  `WEB_IMAGE=ghcr.io/lucasetculbra/django-upload-stack:latest` no ambiente, um
  `docker compose up -d` (sem `--build`) usa a imagem do registry produzida pelo workflow
  descrito em [CI/CD](ci-cd.md).

#### `env_file` **e** `environment` juntos — quem ganha

O `web` recebe o `.env` inteiro, porque ele realmente precisa de tudo: `SECRET_KEY`,
`DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `MAX_UPLOAD_MB`, `LOG_LEVEL` e as
credenciais do banco.

Em cima disso, `environment` força `POSTGRES_HOST: db` e `POSTGRES_PORT: 5432`. A ordem de
precedência do Compose é: `environment` **sobrescreve** `env_file`, e ambos sobrescrevem o
`ENV` da imagem. Isso é deliberado porque o mesmo `.env` é lido em contextos diferentes: na
CI, a suíte roda contra um *service container* do PostgreSQL publicado em `localhost`, e é
natural alguém deixar `POSTGRES_HOST=localhost` no arquivo. Dentro do Compose, `localhost`
seria o **próprio contêiner web** — conexão recusada. Fixando no `environment`, o valor
correto (`db`, o nome DNS do serviço na rede `backend`) vence sempre.

!!! danger "Não defina `MEDIA_ROOT` ou `STATIC_ROOT` no `.env`"
    A imagem já define `MEDIA_ROOT=/app/media` e `STATIC_ROOT=/app/staticfiles` no bloco
    `ENV` do Dockerfile, e são exatamente esses caminhos que o Compose monta como volume
    nomeado. Como `env_file` sobrescreve o `ENV` da imagem, apontar `MEDIA_ROOT` para
    qualquer outro caminho no `.env` faz o Django gravar os uploads **fora** do volume, na
    camada gravável do contêiner — e o próximo `down`+`up` apaga tudo, silenciosamente.

#### `volumes` — os dois volumes que dão nome ao projeto

```yaml
      - media_data:/app/media
      - static_data:/app/staticfiles
```

`media_data` guarda o que os usuários enviam (`UploadedFile.file` grava em
`uploads/%Y/%m/` sob o `MEDIA_ROOT`). `static_data` guarda o resultado do `collectstatic`.
Ambos são montados **para leitura e escrita** aqui, e montados `:ro` no Nginx. A mecânica
completa de persistência, inclusive como inspecionar e fazer backup dos volumes, está em
[Persistência e volumes](persistencia.md).

#### `expose` e a ausência de `ports` — por que o Gunicorn não é publicado

```yaml
    expose:
      - "8000"
```

`expose` **não** publica nada no host: é documentação declarativa (a porta que o serviço
oferece à rede interna). Dentro da rede `backend`, o Nginx alcança `web:8000`
independentemente disso — em uma rede bridge definida pelo usuário, todas as portas dos
contêineres são acessíveis entre si.

A ausência de `ports:` é a decisão de segurança central do stack. Se houvesse
`ports: ["8000:8000"]`:

- o Gunicorn ficaria acessível direto de fora, **contornando o Nginx** e, com ele, o
  `client_max_body_size` (o limite de upload deixaria de ser aplicado na borda) e todos os
  cabeçalhos de segurança aplicados em `/media/`;
- o Django passaria a servir `/media/` e `/static/` por conta própria — que, com
  `DEBUG=0`, simplesmente **não funciona**, gerando 404 em arquivos que existem;
- haveria dois caminhos de entrada com comportamentos diferentes para a mesma aplicação:
  a receita para "funciona na 8000 mas quebra na 8080".

Com um único ponto de entrada (`8080` → Nginx), o que você testa é o que está em produção.

#### `depends_on` com `condition: service_healthy`

```yaml
    depends_on:
      db:
        condition: service_healthy
```

A forma curta (`depends_on: [db]`) só garante **ordem de criação**: o Compose inicia o
`db` antes do `web`, mas "iniciado" significa apenas que o processo existe — o PostgreSQL
pode levar mais alguns segundos até aceitar conexões. `condition: service_healthy` amarra a
partida do `web` ao healthcheck descrito acima, o que elimina a falha de largada.

!!! note "Isso não torna o laço de espera do `entrypoint.sh` redundante"
    `depends_on` só age na **inicialização**. Se o `db` reiniciar mais tarde (crash,
    `docker compose restart db`, atualização da imagem), o Compose **não** reinicia nem
    segura o `web`. O laço no entrypoint cobre esse caso e também o cenário de uso do
    `web` fora do Compose. Detalhes na [Parte 3](#parte-3-entrypointsh).

#### `healthcheck` — por que ele manda um `Host: localhost` explícito

O probe é um `CMD` em forma exec (sem shell) que roda Python puro. A escolha do Python não
é capricho: a imagem base `python:3.13-slim` não traz `curl` nem `wget`, e instalar um
deles só para o healthcheck aumentaria a imagem e a superfície de ataque. O interpretador
já está lá, com a `urllib` da biblioteca padrão.

O ponto sutil é o cabeçalho:

```python
req = urllib.request.Request(
    "http://127.0.0.1:8000/healthz/", headers={"Host": "localhost"}
)
```

O Django valida o cabeçalho `Host` de **toda** requisição contra `ALLOWED_HOSTS`
(`django.http.request.HttpRequest.get_host()`); um host não listado resulta em
**`400 Bad Request` / `DisallowedHost`**, e não em 200. Sem o cabeçalho explícito, a
`urllib` mandaria `Host: 127.0.0.1:8000`, e o probe passaria a depender do conteúdo do
`.env` do usuário: bastaria alguém editar `ALLOWED_HOSTS` para o domínio real do deploy e,
sem notar, remover `127.0.0.1` — o healthcheck começaria a receber 400, o contêiner nunca
ficaria `healthy`, o `nginx` (que depende de `web: service_healthy`) nunca subiria e um
`up --wait` estouraria o timeout. Um erro de configuração de hostname viraria uma falha de
orquestração aparentemente sem relação com ele.

Fixando `Host: localhost`, o probe fica preso a um valor previsível — o mesmo que
`.env.example` traz em `ALLOWED_HOSTS=localhost,127.0.0.1,web` e que o próprio
`config/settings.py` usa como padrão quando a variável não é informada.

O endpoint `/healthz/` não é um 200 vazio: a view `healthz` em `uploads/views.py` executa
um `SELECT 1` na conexão e devolve
`{"status": "ok", "database": "ok", "vendor": "postgresql"}` — ou HTTP 503 se o banco
estiver fora. "Healthy", aqui, significa *"o Django respondeu **e** o banco respondeu"*.

!!! warning "Se você alterar `ALLOWED_HOSTS`, mantenha `localhost` e `127.0.0.1`"
    `localhost` é exigido pelo healthcheck do `web`; `127.0.0.1` é exigido pelo
    healthcheck do `nginx` (o `wget` do BusyBox envia `Host: 127.0.0.1`, que o Nginx
    repassa ao Django). Remover qualquer um dos dois derruba a orquestração inteira —
    sintoma e diagnóstico em [Troubleshooting](troubleshooting.md).

Sobre os tempos: `start_period: 30s` é generoso de propósito, porque o entrypoint ainda vai
esperar o banco, aplicar migrações e rodar `collectstatic --clear` antes de o Gunicorn
sequer abrir a porta. `start_interval: 2s` é o complemento elegante: **durante** o
`start_period`, o Docker sonda a cada 2 s em vez de a cada `interval: 10s`. Na prática, o
contêiner é marcado como saudável poucos segundos depois de ficar pronto, em vez de esperar
o próximo tique de 10 s — o que encurta visivelmente o `docker compose up --wait`.

### Serviço `nginx` — o único com porta publicada

```yaml
  nginx:
    build:
      context: ./nginx
    image: django-upload-stack-nginx:local
    container_name: dus-nginx
    restart: unless-stopped
    ports:
      - "${NGINX_PORT:-8080}:80"
    volumes:
      # Read-only: Nginx only serves these, the app writes them.
      - media_data:/vol/media:ro
      - static_data:/vol/static:ro
    depends_on:
      # Also guarantees "web" mounts the media/static volumes first, so an empty volume
      # is seeded with the app user's ownership instead of root's.
      web:
        condition: service_healthy
    healthcheck:
      # Goes through the proxy to Django, so "healthy" means the whole chain answers.
      # Without this, `up --wait` returns while Nginx is still binding and the first
      # request can fail with an empty reply.
      test: ["CMD", "wget", "--quiet", "--spider", "http://127.0.0.1/healthz/"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 15s
      start_interval: 1s
    networks: [backend]
```

#### `ports` — a única linha que abre o stack para o mundo

```yaml
      - "${NGINX_PORT:-8080}:80"
```

Leia como `host:contêiner`. O Nginx escuta na 80 **dentro** do contêiner e o Docker
publica isso na porta do host definida por `NGINX_PORT`, com `8080` como padrão via
`:-`. Se a 8080 já estiver ocupada na sua máquina, basta `NGINX_PORT=9090` no `.env` — sem
tocar em nenhum arquivo versionado.

!!! tip "Mudou a porta? Atualize `CSRF_TRUSTED_ORIGINS`"
    A porta faz parte da *origin* para o Django. Trocando para 9090, o `.env` precisa de
    `CSRF_TRUSTED_ORIGINS=http://localhost:9090,http://127.0.0.1:9090`, senão todo POST do
    navegador volta 403. O motivo está explicado em
    [`proxy_set_header Host $http_host`](#proxy_set_header-e-por-que-http_host-e-nao-host).

#### `volumes` montados `:ro`

O Nginx **só lê** os arquivos; quem escreve é a aplicação. Montar `:ro` transforma isso em
garantia do kernel, não em promessa: mesmo que o processo do Nginx seja comprometido, ele
não consegue sobrescrever um arquivo em `/vol/media` nem plantar conteúdo novo lá. Repare
também que os caminhos internos são diferentes dos do `web` (`/vol/media` versus
`/app/media`) — o volume é o mesmo, o ponto de montagem é livre.

Para que o Nginx (que roda como usuário `nginx`) consiga ler o que o Gunicorn (usuário
`app`, uid 1000) escreveu, o `config/settings.py` define explicitamente
`FILE_UPLOAD_PERMISSIONS = 0o644` e `FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755`, em vez de
depender do umask que o processo herdar.

#### `depends_on: web: service_healthy` — dois motivos, não um

O motivo óbvio: não adianta abrir a porta 8080 enquanto o backend ainda está migrando; o
visitante levaria 502.

O motivo registrado no comentário do arquivo é mais interessante e fácil de perder: ele
**garante a ordem de montagem dos volumes**. O Docker popula um volume nomeado vazio a
partir do conteúdo (e da propriedade) do diretório correspondente na imagem, e isso
acontece na **primeira** vez que o volume é montado. Se o `nginx` montasse `media_data`
antes do `web`, o volume seria semeado a partir de `/vol/media` da imagem do Nginx — um
diretório inexistente, criado na hora e pertencente ao `root`. O Gunicorn, rodando como
uid 1000, receberia `Permission denied` ao gravar o primeiro upload. Subindo o `web`
primeiro, o volume nasce com o conteúdo de `/app/media` da imagem da aplicação, que o
Dockerfile cuidadosamente cria com `chown app:app`.

#### Por que o Nginx tem healthcheck

Um proxy reverso não costuma ter healthcheck — ele é a coisa que checa os outros. Aqui ele
existe por dois motivos concretos:

1. **`test` atravessa a cadeia inteira.** `wget --spider http://127.0.0.1/healthz/` não
   testa o Nginx: ele entra pelo `location /`, é encaminhado ao Gunicorn e chega no
   `SELECT 1` da view `healthz`. Um `nginx` `healthy` significa *"Nginx + Gunicorn +
   PostgreSQL responderam a uma requisição real"*. É a checagem ponta a ponta do stack em
   uma linha.
2. **`docker compose up --wait` precisa dela.** Sem healthcheck, o `--wait` considera o
   serviço pronto assim que o contêiner está *running* — o que acontece milissegundos antes
   de o Nginx terminar de fazer o `bind`. O primeiro `curl` depois do `up` pode voltar
   *empty reply from server*. Com o healthcheck, o `--wait` só retorna quando o stack
   realmente atende. O `scripts/smoke_test.sh` depende exatamente disso no passo em que
   recria o stack (`docker compose up -d --wait --wait-timeout 300`).

`--spider` faz o `wget` só verificar o recurso, sem baixar o corpo; `--quiet` evita poluir
o log do healthcheck. Ambos existem no `wget` do BusyBox, que vem na imagem Alpine.

### Volumes e rede

```yaml
volumes:
  postgres_data:
  media_data:
  static_data:

networks:
  backend:
    driver: bridge
```

Três volumes nomeados declarados sem opções — o Docker os cria com o driver `local` e os
prefixa com o nome do projeto. Declarar aqui (em vez de usar bind mounts para pastas do
host) é o que torna a persistência portátil e, em particular, o que evita os problemas de
permissão e de *file locking* de bind mounts em host Windows/OneDrive.

A rede `backend` com driver `bridge` é uma rede definida pelo usuário, e não a bridge
padrão do Docker. A diferença decisiva: **redes definidas pelo usuário têm resolução DNS
automática por nome de serviço**. É graças a ela que `proxy_pass http://web:8000` e
`POSTGRES_HOST=db` funcionam. Na bridge padrão isso não existe — seria preciso recorrer a
`--link`, que está obsoleto.

### Resumo da ordem de inicialização

```text
db (initdb, servidor temporário, servidor definitivo)
      |  healthcheck pg_isready via TCP  -> healthy
      v
web (entrypoint: espera o banco -> migrate -> collectstatic -> exec gunicorn)
      |  healthcheck GET /healthz/ com Host: localhost -> healthy
      v
nginx (bind :80)
      |  healthcheck wget /healthz/ atravessando o proxy -> healthy
      v
docker compose up --wait retorna
```

---

## Parte 2 — o `Dockerfile` multi-stage

```dockerfile
# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------------------
# Stage 1: build the virtualenv. Keeping pip and the build tooling out of the final image
# makes it smaller and reduces its attack surface.
# ---------------------------------------------------------------------------------------
FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
```

### Estágio 1 — `builder`

A linha `# syntax=docker/dockerfile:1` fixa o frontend do BuildKit na série 1 estável, de
modo que a sintaxe usada aqui continue sendo interpretada da mesma forma em qualquer
versão do Docker.

O estágio inteiro existe para produzir **um único artefato**: o virtualenv em `/opt/venv`.
Por que um venv dentro de um contêiner, se o contêiner já é isolado? Porque ele cria um
diretório **autocontido e copiável**: `COPY --from=builder /opt/venv /opt/venv` traz todas
as dependências para o estágio final em uma linha, sem arrastar junto o `pip`, o
`setuptools` e os caches do processo de instalação.

As três variáveis do `ENV` são higiene de build: `PIP_DISABLE_PIP_VERSION_CHECK=1` remove o
aviso de "há uma versão mais nova do pip" do log; `PIP_NO_CACHE_DIR=1` e o
`--no-cache-dir` redundante no `RUN` evitam que o cache HTTP do pip engorde a camada;
`PYTHONDONTWRITEBYTECODE=1` impede a escrita de `.pyc` durante a instalação.

O ponto de ouro é a ordem: `COPY requirements.txt ./` vem **antes** de qualquer código da
aplicação. Como o Docker invalida o cache de uma camada em diante, editar `uploads/views.py`
não invalida o `pip install` — o rebuild reaproveita a camada pesada e leva segundos em vez
de minutos. Esse é também o motivo de a CI usar `cache-to: type=gha,mode=max`: o `mode=max`
exporta as camadas dos estágios intermediários, que é onde mora justamente esse
`pip install`.

As dependências de runtime são exatamente três, todas com versão fixa em
`requirements.txt`: `Django==5.2.17`, `psycopg[binary]==3.3.6` e `gunicorn==26.2.0`. O
extra `[binary]` do psycopg traz rodas pré-compiladas — é o que dispensa instalar `gcc` e
`libpq-dev` no builder.

### Estágio 2 — `runtime`

```dockerfile
# ---------------------------------------------------------------------------------------
# Stage 2: runtime image. Runs as a non-root user and only carries the virtualenv
# plus the application code.
# ---------------------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    DJANGO_DB=postgres \
    MEDIA_ROOT=/app/media \
    STATIC_ROOT=/app/staticfiles

# uid/gid 1000 keeps file ownership predictable on the named volumes.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/sh app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# These directories become the mount points for the media/static named volumes. Docker
# seeds an empty named volume from the image's directory, so creating them with the right
# owner here is what lets the non-root process write uploads later.
RUN mkdir -p /app/media /app/staticfiles && chown -R app:app /app

COPY --chown=app:app manage.py entrypoint.sh ./
COPY --chown=app:app config ./config
COPY --chown=app:app uploads ./uploads

# Strip CRLF (the repository is authored on Windows) and set the executable bit, which
# Git on Windows does not preserve.
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod 0755 /app/entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
```

#### O bloco `ENV`, item por item

| Variável | Por quê |
| --- | --- |
| `PATH="/opt/venv/bin:$PATH"` | Faz `python`, `gunicorn` e `pip` resolverem para o venv copiado. É o equivalente permanente de um `source activate`, que não sobreviveria entre instruções `RUN`/`CMD`. |
| `PYTHONUNBUFFERED=1` | Sem ela, o stdout do Python fica *block-buffered* quando não está num terminal, e as mensagens do `entrypoint.sh` e do Django aparecem em blocos atrasados (ou somem, se o contêiner morrer antes do flush). Com ela, `docker compose logs -f web` mostra o que está acontecendo em tempo real. |
| `PYTHONDONTWRITEBYTECODE=1` | Não escreve `__pycache__` na camada gravável do contêiner: nada útil, já que o contêiner é efêmero. |
| `DJANGO_SETTINGS_MODULE=config.settings` | Permite rodar `python manage.py ...` e o Gunicorn sem repetir `--settings`. |
| `DJANGO_DB=postgres` | O padrão seguro. `config/settings.py` **exige** escolha explícita: com `postgres`, as variáveis `POSTGRES_*` viram obrigatórias e a ausência de qualquer uma levanta `ImproperlyConfigured`. Não existe *fallback* silencioso para SQLite — ele gravaria o banco na camada do contêiner e faria os dados sumirem no `down`, exatamente o contrário do que o projeto demonstra. |
| `MEDIA_ROOT=/app/media` | Caminho absoluto que casa com a montagem de `media_data`. O `settings.py` usa caminhos relativos ao projeto como padrão (para o `pytest` e o `runserver` no Windows) e deixa a imagem sobrescrever. |
| `STATIC_ROOT=/app/staticfiles` | Idem, para o destino do `collectstatic` e a montagem de `static_data`. |

#### O usuário não-root com uid/gid 1000

```dockerfile
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/sh app
```

Rodar como `root` dentro de um contêiner é um risco desnecessário: qualquer execução de
código arbitrária na aplicação começa com privilégio total no namespace do contêiner. Mas
o detalhe que faz diferença **aqui** é o uid/gid **fixo em 1000**, e não um valor sorteado
pelo sistema.

Os arquivos dentro de um volume nomeado são gravados com o **uid numérico** do processo —
o nome `app` não existe fora do contêiner. Fixando 1000:

- reconstruir a imagem (ou trocar a imagem base) não muda o dono dos arquivos já gravados
  no volume, então o Gunicorn continua conseguindo escrever;
- 1000 é o uid do primeiro usuário comum em praticamente toda distribuição Linux, o que
  torna trivial inspecionar o volume a partir do host em um deploy Linux;
- `--shell /bin/sh` é o suficiente para `docker compose exec web sh` funcionar quando você
  precisar investigar algo por dentro.

#### Pré-criar `/app/media` e `/app/staticfiles` — a linha que semeia os volumes

```dockerfile
RUN mkdir -p /app/media /app/staticfiles && chown -R app:app /app
```

Esta é, provavelmente, a instrução mais importante do arquivo para o objetivo do projeto.

Quando o Docker monta um volume nomeado **vazio** sobre um caminho que **existe** na
imagem, ele copia para o volume o conteúdo e, junto, a **propriedade e as permissões**
daquele diretório. Se o caminho **não** existisse na imagem, o Docker o criaria na hora,
pertencente a `root:root` — e o processo rodando como uid 1000 receberia
`PermissionError: [Errno 13] Permission denied` no primeiro upload, um erro que só aparece
em runtime e parece não ter nada a ver com o Dockerfile.

Criando os diretórios com dono `app:app` **antes** de o volume existir, a semeadura
acontece com a propriedade certa e tudo funciona na primeira tentativa. É também por isso
que o `nginx` declara `depends_on: web`, como explicado na Parte 1: ele garante que quem
semeia o volume é a imagem da aplicação.

#### O `sed -i` de CRLF e o `chmod 0755`

```dockerfile
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod 0755 /app/entrypoint.sh
```

Duas cicatrizes de desenvolver em Windows, ambas reais:

1. **Fim de linha.** O `entrypoint.sh` começa com `#!/bin/sh`. Se o arquivo estiver com
   terminações CRLF, o kernel Linux lê o interpretador como `/bin/sh\r` — que não existe —
   e o contêiner morre com a mensagem lendária e enganosa
   `exec /app/entrypoint.sh: no such file or directory` (o arquivo está lá; o
   *interpretador* é que não). O `sed -i 's/\r$//'` remove o `\r` final de cada linha,
   tornando o build imune a como o Git configurou `core.autocrlf` na máquina de quem
   clonou. O repositório também traz um `.gitattributes` com `*.sh text eol=lf` como
   primeira linha de defesa, mas o `sed` garante o resultado mesmo se essa configuração
   não for respeitada.
2. **Bit de execução.** O sistema de arquivos do Windows não tem o bit de executável, e o
   Git nessa plataforma não o preserva. Sem o `chmod 0755`, o `ENTRYPOINT` falharia com
   `permission denied`. Aplicar o `chmod` dentro do build torna o resultado independente
   do host.

!!! tip "Sintoma × causa"
    `exec ...: no such file or directory` em um script que visivelmente existe é quase
    sempre CRLF no shebang. `permission denied` é quase sempre bit de execução. Estas duas
    linhas eliminam as duas classes de erro de uma vez.

#### `ENTRYPOINT` e `CMD`, ambos em forma exec

A separação tem um propósito claro:

- **`ENTRYPOINT`** é a parte que *sempre* roda: esperar o banco, migrar, coletar estáticos.
- **`CMD`** é o processo padrão, e é **substituível**. Como o `ENTRYPOINT` termina em
  `exec "$@"`, qualquer comando passado no `docker run`/`docker compose run` toma o lugar
  do Gunicorn e continua herdando toda a preparação. Por isso
  `docker compose run --rm web python manage.py createsuperuser` funciona com o banco já
  migrado.

A **forma exec** (lista JSON `["gunicorn", ...]`) em vez da forma shell
(`CMD gunicorn ...`) é decisiva para o desligamento limpo. Na forma shell, o Docker
envolveria tudo em `/bin/sh -c "..."`; o `sh` viraria o processo que recebe o `SIGTERM` do
`docker compose stop` e **não o repassaria** ao Gunicorn. O resultado seria um timeout de
10 s seguido de `SIGKILL` — conexões cortadas no meio e uploads em andamento truncados. Na
forma exec, o `SIGTERM` chega direto ao Gunicorn, que fecha o socket de escuta, deixa os
workers terminarem os requests em andamento e sai. Os `\` no fim das linhas são apenas
continuação de linha dentro da lista JSON, para o `CMD` caber legível na página.

#### As flags do Gunicorn

| Flag | Efeito e motivo |
| --- | --- |
| `--bind 0.0.0.0:8000` | Escuta em **todas** as interfaces do contêiner. `127.0.0.1:8000` seria inalcançável a partir do contêiner do Nginx, porque `localhost` em um contêiner é o *loopback dele mesmo*, não do host nem da rede. Como não há `ports:` no serviço `web`, "todas as interfaces" significa, na prática, "só a rede `backend`". |
| `--workers 3` | Três processos worker sincronizados. Cada um carrega o Django inteiro na memória, então o número troca RAM por concorrência. Três é um padrão sóbrio para o stack didático e para um runner de CI; em produção, a heurística usual do Gunicorn é `2 × núcleos + 1`. Com workers sync, um upload longo ocupa um worker inteiro — daí o `--timeout` folgado abaixo. |
| `--timeout 120` | Um worker que não responder em 120 s é morto e reiniciado pelo master. O padrão do Gunicorn é 30 s, curto demais para um upload de até 100 MB vindo de uma conexão doméstica: o worker fica ocupado recebendo o corpo da requisição durante todo o envio. Este valor está deliberadamente **espelhado** no `proxy_read_timeout 120s` do Nginx. |
| `--access-logfile -` | Log de acesso no **stdout**. |
| `--error-logfile -` | Log de erro no **stderr**. |

O `-` nas duas últimas é o que integra a aplicação ao ecossistema do Docker: os logs vão
para o log do contêiner (`docker compose logs -f web`), em vez de para um arquivo dentro do
sistema de arquivos efêmero, onde ninguém os veria e onde eles cresceriam sem rotação.
Combinado com `PYTHONUNBUFFERED=1`, isso dá observabilidade imediata.

### Estágio 3 — `test`

```dockerfile
# ---------------------------------------------------------------------------------------
# Stage 3: image with the test tooling, used by docker-compose.test.yml.
# ---------------------------------------------------------------------------------------
FROM runtime AS test

USER root
COPY requirements.txt requirements-dev.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements-dev.txt
USER app
```

Repare no `FROM runtime` (e não `FROM python:3.13-slim`): a imagem de teste é a **imagem de
produção mais as ferramentas de teste**, então a suíte roda contra exatamente o mesmo
Python, as mesmas versões de dependência e o mesmo usuário que atenderão em produção. É
o oposto do anti-padrão "imagem de teste parecida com a de produção".

O `USER root` temporário é necessário porque o venv em `/opt/venv` pertence ao `root`; o
usuário `app` não conseguiria instalar nada nele. O `USER app` no fim devolve o privilégio
mínimo, de modo que os testes rodam sem ser root — inclusive os que gravam arquivos, o que
mantém a checagem de permissões honesta.

O `pyproject.toml` é copiado porque é lá que mora a configuração do pytest:

```ini
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings_test"
python_files = ["test_*.py"]
testpaths = ["uploads/tests"]
addopts = "-ra"
```

O `requirements-dev.txt` inclui `-r requirements.txt` e acrescenta `pytest`,
`pytest-django` e `ruff`.

#### O overlay `docker-compose.test.yml`

```yaml
# Overlay that swaps the web image for the "test" build stage (which also installs
# pytest) and replaces the entrypoint so the suite runs against the real PostgreSQL
# service instead of SQLite.
#
#   docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm web
services:
  web:
    build:
      target: test
    image: django-upload-stack-web:test
    entrypoint: []
    command: ["pytest"]
    restart: "no"
    healthcheck:
      disable: true
```

Um arquivo de *override* não substitui o `docker-compose.yml`: ele é mesclado por cima, e
só as chaves listadas mudam. Tudo que não aparece aqui — `env_file`, `depends_on`,
`networks`, volumes — continua valendo. Cada chave tem um motivo:

- **`build.target: test`** constrói o terceiro estágio em vez de parar no `runtime`;
- **`image: django-upload-stack-web:test`** evita sobrescrever a tag `:local` de produção,
  para que as duas imagens possam coexistir;
- **`entrypoint: []`** *zera* o `ENTRYPOINT` da imagem. Sem isso, o `pytest` seria passado
  como argumento para o `entrypoint.sh`, que esperaria o banco, rodaria `migrate` e
  `collectstatic` antes — trabalho inútil para a suíte, que cria e destrói o próprio banco
  de teste;
- **`command: ["pytest"]`** é o que efetivamente roda;
- **`restart: "no"`** porque um processo de teste **deve** terminar. Com o
  `unless-stopped` herdado, uma suíte que falhasse seria reiniciada em laço;
- **`healthcheck: disable: true`** porque não há servidor HTTP escutando na 8000 durante os
  testes; o probe herdado falharia sempre.

Como `docker compose run` respeita `depends_on`, o serviço `db` sobe e fica `healthy` antes
de o `pytest` começar — e é por isso que o comentário diz que a suíte roda contra o
PostgreSQL de verdade. O `config/settings_test.py` usa `os.environ.setdefault(...)`, que
**não** sobrescreve valores já presentes: por isso o padrão local é SQLite, mas o
`env_file: .env` herdado do serviço `web` (com `DJANGO_DB=postgres` e os `POSTGRES_*`)
prevalece dentro do Compose.

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm web
```

---

## Parte 3 — `entrypoint.sh`

```bash
#!/bin/sh
# Container entrypoint: wait for PostgreSQL, apply migrations, collect static files,
# then hand over to the process given as CMD (Gunicorn).
set -e

if [ "${DJANGO_DB:-postgres}" = "postgres" ]; then
    echo "[entrypoint] waiting for postgres at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432} ..."
    attempt=1
    max_attempts="${DB_WAIT_ATTEMPTS:-60}"
    until python -c "
import os, sys
import psycopg

try:
    psycopg.connect(
        dbname=os.environ['POSTGRES_DB'],
        user=os.environ['POSTGRES_USER'],
        password=os.environ['POSTGRES_PASSWORD'],
        host=os.environ.get('POSTGRES_HOST', 'db'),
        port=os.environ.get('POSTGRES_PORT', '5432'),
        connect_timeout=3,
    ).close()
except Exception as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; do
        if [ "$attempt" -ge "$max_attempts" ]; then
            echo "[entrypoint] database still unreachable after ${max_attempts} attempts, giving up" >&2
            exit 1
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    echo "[entrypoint] database is ready"
fi

echo "[entrypoint] applying migrations"
python manage.py migrate --noinput

echo "[entrypoint] collecting static files"
python manage.py collectstatic --noinput --clear

echo "[entrypoint] starting: $*"
exec "$@"
```

### `#!/bin/sh` e `set -e`

É `sh`, não `bash`: a imagem `python:3.13-slim` é Debian e tem `bash`, mas manter o script
em POSIX shell puro deixa a porta aberta para uma base Alpine sem nenhuma edição.

`set -e` aborta o script na primeira falha de comando. Sem ele, um `migrate` que quebrasse
seria apenas uma linha vermelha no log, e o Gunicorn subiria mesmo assim contra um banco em
estado inconsistente — todo request devolvendo 500 com o contêiner marcado como "rodando".
Com `set -e`, o contêiner morre, e a causa fica no topo de `docker compose logs web`.

### O laço de espera pelo banco

A espera testa a **conexão real** que o Django vai usar: o mesmo driver (`psycopg`), as
mesmas credenciais, o mesmo banco. Não é um `nc -z host 5432`, que apenas confirma que
*alguma coisa* aceita TCP naquela porta — isso passaria mesmo com uma senha errada ou com o
banco `upload_stack` ainda não criado. Aqui, se `POSTGRES_PASSWORD` estiver errada, o laço
falha de verdade e o contêiner para com uma mensagem, em vez de quebrar depois no
`migrate`.

O `connect_timeout=3` impede que uma tentativa fique pendurada e trave o laço inteiro.

#### Por que a espera é **limitada** e sai com `exit 1`

```sh
        if [ "$attempt" -ge "$max_attempts" ]; then
            echo "[entrypoint] database still unreachable after ${max_attempts} attempts, giving up" >&2
            exit 1
        fi
```

`max_attempts` vem de `DB_WAIT_ATTEMPTS`, com padrão 60 — ou seja, cerca de 60 s
(um `sleep 1` por tentativa, mais o tempo de cada tentativa de conexão).

Um `until ...; do sleep 1; done` **sem limite** é o padrão que se vê na maioria dos
tutoriais, e é uma armadilha: se a senha estiver errada, se `POSTGRES_HOST` apontar para
lugar nenhum ou se o volume do banco estiver corrompido, o contêiner fica girando **para
sempre**, com o status `starting`, sem erro visível. Numa CI, isso vira um job que só morre
no timeout de 20 minutos, sem diagnóstico. Falhar alto depois de um limite conhecido, com
uma mensagem explícita em `stderr` e código de saída 1, transforma "está travado" em "está
quebrado, e o log diz por quê" — e, com `restart: unless-stopped`, o Docker ainda tenta de
novo, então uma indisponibilidade genuinamente transitória continua se resolvendo sozinha.

O `2>/dev/null` no `until` silencia o texto da exceção **durante** as tentativas (senão
cada segundo despejaria um traceback de "connection refused" no log). A mensagem final de
desistência, essa, vai para `stderr` com `>&2`.

#### Por que isso não é redundante com `depends_on: condition: service_healthy`

Essa é a pergunta certa a fazer, e a resposta é precisa: **`depends_on` só ordena a
partida inicial**.

| Cenário | `depends_on` protege? | O laço protege? |
| --- | --- | --- |
| Primeiro `up` com o banco ainda inicializando | Sim | Sim (redundância barata) |
| `docker compose restart db` com o `web` já rodando | **Não** | Sim, se o `web` também reiniciar |
| Banco reiniciado por crash/OOM, `web` reiniciado por `restart: unless-stopped` | **Não** | **Sim** |
| Imagem rodada fora do Compose (`docker run`, Kubernetes, Swarm) | Não existe | **Sim** |
| Senha errada no `.env` | Não detecta | **Sim**, e falha com mensagem |

Em resumo: `depends_on` é uma garantia do orquestrador, válida uma vez; o laço é uma
propriedade **da imagem**, válida em toda partida. A imagem publicada em
`ghcr.io/lucasetculbra/django-upload-stack` precisa ser correta por si, sem depender de
quem a orquestra.

O bloco inteiro é condicionado a `DJANGO_DB=postgres`, então a imagem também parte
normalmente com `DJANGO_DB=sqlite` (usado apenas em testes locais), sem esperar por um
banco que não existe.

### `migrate --noinput`

```sh
python manage.py migrate --noinput
```

`--noinput` (equivalente a `--no-input`) é obrigatório em um entrypoint: não há terminal
para responder a um prompt do Django, e sem a flag o comando bloquearia esperando um `yes`
que nunca chega.

Migrar na partida, e não em um passo manual, é o que faz `docker compose up -d` ser
suficiente para um ambiente novo. O `migrate` é idempotente — ele consulta a tabela
`django_migrations` e aplica apenas o que falta —, então reiniciar o contêiner dez vezes
não faz nada além de imprimir "No migrations to apply".

!!! warning "Limite conhecido desta abordagem"
    Com várias réplicas do `web` partindo ao mesmo tempo, todas rodariam `migrate` em
    paralelo. O PostgreSQL protege a maior parte do caminho com locks de DDL, mas o padrão
    recomendado para escalar é extrair as migrações para um job dedicado (um `docker
    compose run --rm web python manage.py migrate` antes do deploy). Para este stack de uma
    réplica, migrar no entrypoint é a escolha certa.

### `collectstatic --noinput --clear`

```sh
python manage.py collectstatic --noinput --clear
```

Esta é a decisão de design mais discutível do arquivo à primeira vista — "por que não
rodar `collectstatic` no *build*, como manda o manual?" — e tem uma resposta específica
para **este** stack.

`STATIC_ROOT` é `/app/staticfiles`, e esse caminho é um **volume nomeado**. Um volume
nomeado é semeado a partir da imagem **uma única vez**, quando está vazio. Se o
`collectstatic` rodasse no `docker build`:

1. a primeira execução copiaria os assets para o volume e tudo funcionaria;
2. você faria um `docker compose build` com CSS novo;
3. no `up` seguinte, o volume **já não está vazio** — o Docker não o re-semeia. O Nginx
   continuaria servindo os arquivos antigos, indefinidamente, enquanto a imagem nova
   contém os novos. O clássico "mudei o CSS, dei rebuild, e o navegador insiste no antigo",
   que sobrevive até a um `Ctrl+F5` porque o problema não é o cache do navegador: é o
   volume.

Coletando **na partida do contêiner**, o conteúdo do volume é reescrito a cada `up`, e o
`--clear` apaga o destino antes de copiar — o que remove também os arquivos órfãos, de
assets deletados ou renomeados, que de outro modo ficariam para sempre no volume.

O custo é alguns segundos por partida; é exatamente para acomodá-los (somados à espera do
banco e às migrações) que o healthcheck do `web` tem `start_period: 30s`.

### `exec "$@"` — a última linha, e a mais importante

```sh
exec "$@"
```

`"$@"` expande para os argumentos recebidos pelo `ENTRYPOINT`, que são exatamente o `CMD`
(o Gunicorn e suas flags) ou o que você tiver passado no `docker compose run`. As aspas
preservam cada argumento como uma palavra, mesmo que contenha espaços.

O `exec` é o que importa: em vez de criar um processo filho, ele **substitui** o processo
do shell pelo novo programa, reaproveitando o mesmo PID. Como o `ENTRYPOINT` roda como PID
1 dentro do contêiner, o Gunicorn passa a ser o PID 1. Sem o `exec`:

- o `sh` continuaria sendo o PID 1 e o Gunicorn seria um filho;
- o `SIGTERM` do `docker compose stop`/`down` iria para o `sh`, que **não repassa sinais**
  aos filhos. O Gunicorn nunca saberia que precisa desligar, o Docker esperaria 10 s e
  enviaria `SIGKILL` — requests em andamento cortados, uploads truncados, conexões do
  PostgreSQL deixadas para trás;
- em contêineres de vida curta, também apareceriam processos zumbis, já que um `sh` comum
  não colhe filhos órfãos como um init de verdade.

Com o `exec`, o desligamento é gracioso: `SIGTERM` chega ao master do Gunicorn, que para de
aceitar conexões, deixa os workers terminarem o que estão fazendo e sai com código 0 —
bem dentro do prazo padrão de 10 s do Docker.

---

## Parte 4 — `nginx/default.conf` e `nginx/Dockerfile`

```nginx
# Reverse proxy for the Gunicorn container, plus direct serving of the media and static
# volumes. This is the only service published to the host.

server {
    listen 80;
    server_name _;

    # Must be >= the application's MAX_UPLOAD_MB so Nginx does not reject a request that
    # Django would have accepted (413 before the app ever sees it).
    client_max_body_size 100M;

    # Docker's embedded DNS. Resolving the upstream at request time (via a variable in
    # proxy_pass) means recreating the "web" container does not leave Nginx serving 502s
    # with a stale IP until it is restarted.
    resolver 127.0.0.11 valid=10s ipv6=off;

    access_log /var/log/nginx/access.log;
    error_log  /var/log/nginx/error.log warn;

    # Files uploaded by users are served straight from the media volume. They are
    # untrusted content on the same origin as the app, so scripting is disabled and the
    # browser is told to download them instead of rendering them.
    location /media/ {
        alias /vol/media/;
        add_header X-Content-Type-Options nosniff always;
        add_header Content-Security-Policy "default-src 'none'; sandbox" always;
        add_header Content-Disposition attachment always;
        access_log off;
        expires 1h;
    }

    # Static assets produced by collectstatic.
    location /static/ {
        alias /vol/static/;
        access_log off;
        expires 7d;
    }

    # Everything else goes to Gunicorn over the internal compose network.
    location / {
        set $upstream http://web:8000;
        proxy_pass $upstream;

        # $http_host preserves the port ("localhost:8080"); $host would drop it and make
        # Django's CSRF origin check reject every browser POST.
        proxy_set_header Host              $http_host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_redirect off;
        # Matches "gunicorn --timeout 120" so Nginx does not return 504 first.
        proxy_read_timeout 120s;
        proxy_connect_timeout 5s;
    }
}
```

### `listen 80` e `server_name _`

O Nginx escuta na 80 **dentro** do contêiner; o mapeamento para a 8080 do host é trabalho
do Compose. `server_name _` é o *catch-all* convencional: `_` não é um hostname válido, de
modo que nenhum pedido pode casar com ele "por nome" — ele só é usado por ser o único (e
portanto o padrão) bloco `server` da configuração. É o que se quer em um proxy que atende
`localhost`, `127.0.0.1` e o IP da máquina indistintamente.

### `client_max_body_size 100M` e a sincronia com `MAX_UPLOAD_MB`

O Nginx recusa, com **`413 Request Entity Too Large`**, qualquer corpo maior que esse
limite — e recusa **antes** de encaminhar um único byte ao Gunicorn. O padrão do Nginx é
apenas `1m`, o que faria o stack rejeitar quase todos os uploads reais sem que nada
aparecesse no log do Django.

Este valor precisa ser mantido em sincronia com `MAX_UPLOAD_MB` (padrão `100` no
`.env.example`), que o `config/settings.py` converte em `MAX_UPLOAD_BYTES` e usa tanto em
`DATA_UPLOAD_MAX_MEMORY_SIZE` quanto na validação do formulário:

```python
def clean_file(self):
    uploaded = self.cleaned_data["file"]
    if uploaded.size > settings.MAX_UPLOAD_BYTES:
        raise forms.ValidationError(
            f"O arquivo excede o limite de {settings.MAX_UPLOAD_MB} MB."
        )
    return uploaded
```

A regra de ouro é `client_max_body_size >= MAX_UPLOAD_MB`, e a razão é a qualidade do erro
que o usuário recebe:

| Configuração | O que o usuário vê |
| --- | --- |
| Nginx **maior ou igual** ao Django | Página de erro do formulário, em português, dizendo qual é o limite. É o comportamento desejado. |
| Nginx **menor** que o Django | Uma página 413 crua do Nginx. O Django nunca é chamado, o `clean_file` nunca roda, a mensagem amigável nunca aparece — e o log da aplicação não registra nada. |

!!! warning "Aumentou `MAX_UPLOAD_MB`? Edite as duas pontas"
    `MAX_UPLOAD_MB` no `.env` **e** `client_max_body_size` em `nginx/default.conf`. Como a
    configuração é assada na imagem, a segunda mudança exige
    `docker compose up -d --build nginx`. O `.env.example` e o `settings.py` trazem esse
    lembrete inline justamente porque é fácil mexer em um e esquecer o outro.

### `resolver 127.0.0.11` + variável no `proxy_pass` — o truque anti-502

```nginx
    resolver 127.0.0.11 valid=10s ipv6=off;
...
        set $upstream http://web:8000;
        proxy_pass $upstream;
```

Esta é a parte da configuração que mais parece supérflua e mais evita dor de cabeça.

**O problema.** Quando o `proxy_pass` recebe um nome de host **literal**
(`proxy_pass http://web:8000;`), o Nginx resolve esse nome **uma única vez, ao carregar a
configuração**, e guarda o endereço IP na memória pelo resto da vida do processo. Em
Docker, o IP de um contêiner **muda quando ele é recriado** — e recriar é a operação mais
banal que existe: `docker compose up -d --build web`, `docker compose restart` que troque o
contêiner, um deploy de imagem nova. A partir daí, o Nginx segue tentando o IP antigo e
devolve **502 Bad Gateway** a todos os requests, até alguém reiniciar o Nginx. É um dos
"mistérios" mais comuns de stacks Docker, e o diagnóstico enganoso é "o Django caiu" —
quando, na verdade, o Django está ótimo e quem está desatualizado é o cache de DNS do
proxy.

**A solução, em duas partes.** Ela só funciona com as duas juntas:

1. **`set $upstream http://web:8000;` + `proxy_pass $upstream;`** — a presença de uma
   variável no `proxy_pass` muda o comportamento do Nginx: ele deixa de resolver o nome na
   inicialização e passa a resolvê-lo **em tempo de requisição**.
2. **`resolver 127.0.0.11 ...`** — usar uma variável exige declarar um resolvedor, e
   `127.0.0.11` é o endereço fixo do **servidor DNS embutido do Docker**, presente em toda
   rede definida pelo usuário. É ele que sabe o IP atual do serviço `web`.

Os parâmetros: `valid=10s` limita o cache de cada resposta DNS a 10 segundos (em vez de
respeitar o TTL informado), então a janela de erro após uma recriação é de, no máximo, 10 s
— e não infinita. `ipv6=off` impede que o Nginx faça também uma consulta AAAA; como a rede
`backend` é IPv4, essa consulta só traria latência extra e ruído de `NXDOMAIN` no
`error.log`.

!!! note "Detalhe fino: por que `proxy_pass $upstream;` sem URI no fim"
    Quando o `proxy_pass` contém uma variável e **nenhum** componente de URI, o Nginx
    encaminha a URI original da requisição sem reescrevê-la. Como este é o `location /`,
    é exatamente o que se quer: `/admin/login/` chega ao Django como `/admin/login/`.
    Acrescentar uma barra final (`proxy_pass $upstream/;`) mudaria a semântica e quebraria
    o roteamento.

### `proxy_set_header` — e por que `$http_host` e não `$host`

```nginx
        proxy_set_header Host              $http_host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
```

Sem esse bloco, o Django enxergaria todo mundo como vindo do IP do contêiner do Nginx e
acharia que está sendo acessado em `web:8000`. Cada linha resolve um pedaço:

| Cabeçalho | Valor | Para quê |
| --- | --- | --- |
| `Host` | `$http_host` | Preserva o host **com a porta**, como o navegador enviou. |
| `X-Real-IP` | `$remote_addr` | O IP de quem conectou no Nginx (o cliente real). |
| `X-Forwarded-For` | `$proxy_add_x_forwarded_for` | Acrescenta `$remote_addr` ao valor já existente, preservando a cadeia de proxies em vez de sobrescrevê-la. |
| `X-Forwarded-Proto` | `$scheme` | `http` ou `https` conforme o esquema recebido pelo Nginx. |

**A diferença entre `$host` e `$http_host`** é o detalhe que decide se o site funciona ou
não:

- `$http_host` é o cabeçalho `Host` **cru**, exatamente como o cliente mandou —
  `localhost:8080`, com a porta.
- `$host` é uma versão normalizada pelo Nginx: em minúsculas e **sem a porta** —
  `localhost`.

Por que isso quebra o Django: em requisições POST, o `CsrfViewMiddleware` compara a origem
da requisição (`Origin`, ou `Referer` em HTTP) com a origem esperada, montada a partir do
host do request e conferida contra `CSRF_TRUSTED_ORIGINS`. O navegador manda
`Origin: http://localhost:8080`. Com `$host`, o Django se vê em `http://localhost` (porta
80 implícita), a comparação falha, e **todo formulário do navegador retorna 403** com
`Origin checking failed` — inclusive o formulário de upload, que é o ponto do projeto.
Como o stack roda em porta não padrão por construção, `$http_host` é obrigatório.

Isso casa com a variável `CSRF_TRUSTED_ORIGINS=http://localhost:8080,http://127.0.0.1:8080`
no `.env.example` e com o comentário no `settings.py`
(*"Needed because the app is reached through Nginx on a non-default port"*).

!!! tip "Como verificar sem navegador"
    O `scripts/smoke_test.sh` faz exatamente essa verificação no passo 3: pega o cookie
    `csrftoken` de um `GET /`, e reenvia o POST com `-H "Origin: $BASE"` e
    `-H "Referer: $BASE/"` justamente porque o `curl` puro não manda nenhum dos dois e,
    portanto, **não** exercitaria a checagem de origem. O passo 2 confirma o outro lado:
    um POST sem token tem de voltar 403.

Sobre o `X-Forwarded-Proto`: ele só é *usado* pelo Django se você habilitar
`TRUST_PROXY_SSL_HEADER=1`, e o `settings.py` documenta por que isso é seguro aqui —
o Nginx **sempre sobrescreve** o cabeçalho com `$scheme`, então um cliente não consegue
forjar `X-Forwarded-Proto: https` para enganar a aplicação. Confiar nesse cabeçalho sem
essa garantia seria uma falha de segurança real.

### Timeouts espelhados

```nginx
        proxy_redirect off;
        proxy_read_timeout 120s;
        proxy_connect_timeout 5s;
```

`proxy_read_timeout 120s` é o tempo que o Nginx espera por dados do Gunicorn entre duas
leituras sucessivas. O padrão é 60 s — **metade** do `--timeout 120` do Gunicorn. Essa
assimetria é traiçoeira: numa requisição lenta, o Nginx desistiria aos 60 s e devolveria
**504 Gateway Timeout** enquanto o Gunicorn ainda estivesse processando normalmente; o
trabalho seria concluído e jogado fora, e o log da aplicação não mostraria erro nenhum. Com
os dois em 120 s, quem decide o destino da requisição é a aplicação, e o erro que chega ao
usuário corresponde ao que de fato aconteceu.

`proxy_connect_timeout 5s` é o limite para **estabelecer** a conexão TCP com o upstream —
curto de propósito: dentro da mesma rede bridge, conectar leva milissegundos; cinco
segundos já significam que o contêiner `web` não está lá, e falhar rápido é melhor que
pendurar o cliente.

`proxy_redirect off` mantém intacto o cabeçalho `Location` das respostas do Django. Como o
`Host` repassado já é o correto (`localhost:8080`), o Django gera redirecionamentos certos
sozinho — o redirect pós-upload (padrão *Post/Redirect/Get* da view `index`) depende disso.

### `/media/` e `/static/` com `alias`

```nginx
    location /media/ {
        alias /vol/media/;
        ...
    }

    location /static/ {
        alias /vol/static/;
        ...
    }
```

Esses arquivos **não** passam pelo Gunicorn. O motivo é duplo:

- **Desempenho.** O Nginx serve arquivos estáticos com `sendfile`, sem cópia para o
  espaço de usuário, e com consumo de memória desprezível. Um worker do Gunicorn que
  entregasse um download de 50 MB ficaria ocupado durante todo o envio — com três workers,
  três downloads lentos bastariam para travar o site inteiro.
- **Necessidade.** Com `DEBUG=0`, o Django **não serve** `/static/` nem `/media/`. Sem o
  Nginx, esses caminhos simplesmente retornariam 404.

Sobre `alias` versus `root`: com `alias`, o prefixo casado pelo `location` é **substituído**
pelo caminho indicado — `/media/uploads/2026/09/foo.bin` vira
`/vol/media/uploads/2026/09/foo.bin`. Com `root /vol/media;`, o prefixo seria
**concatenado**, resultando em `/vol/media/media/uploads/...` e um 404. Como os pontos de
montagem (`/vol/media`, `/vol/static`) não repetem o prefixo da URL, `alias` é a diretiva
correta.

!!! warning "As barras finais são obrigatórias"
    Em um `location` com barra final, o `alias` **também** precisa terminar em barra. Um
    `alias /vol/media;` (sem a barra) gera caminhos concatenados errados e é uma fonte
    clássica de 404 — e, em configurações com `location /media` sem a barra, de *path
    traversal*.

`access_log off` em ambos: um único carregamento de página dispara dezenas de requisições
de assets, que afogariam o log sem nenhuma informação útil. `expires 7d` nos estáticos e
`expires 1h` nas mídias fazem o Nginx emitir `Cache-Control`/`Expires`, de modo que o
navegador não rebaixe o mesmo arquivo a cada visita. O prazo mais curto para mídia é
coerente com um conteúdo que pode ser substituído e não tem hash no nome.

### Os cabeçalhos de segurança em `/media/` — evitando XSS armazenado

```nginx
        add_header X-Content-Type-Options nosniff always;
        add_header Content-Security-Policy "default-src 'none'; sandbox" always;
        add_header Content-Disposition attachment always;
```

Esta é a parte de segurança mais importante da configuração, e ela merece ser entendida
como ameaça, não como checklist.

**A ameaça.** O formulário aceita **qualquer** arquivo, e o arquivo é servido de volta pelo
**mesmo host e a mesma porta** da aplicação — `http://localhost:8080/media/...` e
`http://localhost:8080/` são a **mesma origem** para o navegador. Se um visitante enviar um
`.html` com `<script>` — ou um `.svg`, que é XML capaz de conter script e é renderizado
inline por padrão — e outra pessoa abrir esse link, o script roda **dentro da origem da
aplicação**. A partir daí ele pode ler o cookie `csrftoken`, fazer requisições autenticadas
como a vítima (o cookie de sessão viaja junto) e alterar a página. Isso é **XSS
armazenado**, a variante mais perigosa, porque o payload fica hospedado no seu próprio
servidor e é servido a todo mundo que clicar.

**As três defesas, em camadas:**

| Cabeçalho | O que impede |
| --- | --- |
| `X-Content-Type-Options: nosniff` | Proíbe o navegador de "adivinhar" o tipo do conteúdo e tratar como HTML um arquivo declarado como `text/plain` ou `application/octet-stream`. Sem ele, a detecção automática de alguns navegadores executa conteúdo que o servidor nunca disse ser HTML. |
| `Content-Security-Policy: default-src 'none'; sandbox` | `default-src 'none'` bloqueia **todo** carregamento de sub-recurso (scripts, estilos, frames, imagens) a partir do documento. `sandbox`, sem nenhum valor liberado, coloca o recurso em uma **origem opaca**: mesmo que algo execute, não tem acesso aos cookies nem ao `localStorage` da aplicação, nem pode enviar formulários ou navegar o topo. Essa é a defesa que continua valendo mesmo se o tipo do arquivo enganar o navegador. |
| `Content-Disposition: attachment` | Instrui o navegador a **baixar** o arquivo em vez de renderizá-lo. A ameaça desaparece na origem: nenhum HTML ou SVG hostil chega a ser interpretado. |

O `always` em cada `add_header` faz o cabeçalho ser emitido **também** em respostas de erro
(404, 403, 5xx) — sem ele, o Nginx só adiciona cabeçalhos em um conjunto restrito de
códigos de sucesso e redirecionamento.

!!! danger "Trade-off assumido: nada de pré-visualização inline"
    `Content-Disposition: attachment` vale para **todos** os arquivos de `/media/`,
    inclusive imagens legítimas — clicar em uma foto enviada a baixa em vez de exibi-la na
    aba. É uma escolha consciente: para este projeto, a segurança padrão vale mais que a
    conveniência. A forma correta de recuperar a pré-visualização **sem** reabrir o buraco
    é servir as mídias de **outra origem** (um subdomínio dedicado, por exemplo
    `media.exemplo.com`, ou um bucket de object storage), de modo que um script hospedado
    ali não esteja na mesma origem dos cookies da aplicação. Afrouxar os cabeçalhos
    mantendo a mesma origem reintroduz exatamente o XSS armazenado descrito acima.

Do lado do Django, as defesas complementares já estão em `config/settings.py`:
`SECURE_CONTENT_TYPE_NOSNIFF = True` e `X_FRAME_OPTIONS = "DENY"` valem para as respostas
que passam pela aplicação, e o `UploadedFile.save()` guarda o nome original em um campo
separado — o nome real no disco é normalizado pelo storage do Django, que também resolve
colisões acrescentando um sufixo aleatório.

### `nginx/Dockerfile` — por que uma imagem própria

```dockerfile
# syntax=docker/dockerfile:1
# Baking the configuration into an image keeps the stack self-contained and avoids
# single-file bind mounts, which are fragile on Windows/OneDrive hosts.
FROM nginx:1.30-alpine

COPY default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
```

A alternativa comum seria manter a imagem oficial e montar o arquivo:

```yaml
# NÃO é o que este projeto faz:
volumes:
  - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
```

Assar a configuração na imagem foi escolhido por quatro razões concretas:

1. **Bind mount de *arquivo único* é frágil.** O Docker monta arquivos individuais por
   inode. Editores que salvam via "escrever um temporário e renomear" (a maioria, incluindo
   VS Code em algumas configurações) trocam o inode, e o contêiner passa a enxergar o
   arquivo **antigo** — ou nenhum. Em host Windows com o arquivo dentro do OneDrive, some
   ainda a tradução de caminho do WSL2 e a sincronização do OneDrive, que podem substituir o
   arquivo por um *placeholder* de download sob demanda. Um arquivo que existe no Explorer
   e some dentro do contêiner é um jeito muito ruim de perder uma tarde.
2. **A imagem é autocontida e versionada.** `django-upload-stack-nginx:local` carrega a
   configuração que foi testada com ela. Não existe a possibilidade de o contêiner rodar
   com um `default.conf` diferente do que está no commit.
3. **Consistência com a CI e com o deploy.** O job de smoke test da CI roda o mesmo
   `docker compose up --build` e obtém a mesma imagem; não há nada no host de que o stack
   dependa além do próprio repositório.
4. **É onde a validação acontece.** Um erro de sintaxe no `default.conf` faz o contêiner
   falhar na partida com a mensagem exata do Nginx, na hora, em vez de silenciosamente
   rodar uma configuração antiga.

O preço é ter que reconstruir após editar a configuração — um comando:

```bash
docker compose up -d --build nginx
```

`nginx:1.30-alpine` mantém o padrão de tag fixa de versão, e o Alpine deixa a imagem
pequena (e, de quebra, fornece o `wget` do BusyBox que o healthcheck usa). O `EXPOSE 80` é
documentação da porta interna; quem publica é o `ports:` do Compose.

---

## Comandos do dia a dia

Todos são executados na raiz do repositório (onde está o `docker-compose.yml`). A sintaxe é
idêntica no PowerShell e no Git Bash.

| Comando | O que faz |
| --- | --- |
| `docker compose up -d --build --wait` | Constrói o que mudou, sobe tudo em segundo plano e **só retorna quando os três healthchecks passarem**. É o comando de partida padrão. |
| `docker compose up -d` | Sobe sem reconstruir (útil quando só o `.env` mudou). |
| `docker compose ps` | Estado dos contêineres, com a coluna de saúde (`healthy` / `starting` / `unhealthy`). |
| `docker compose logs -f web` | Acompanha o log do Gunicorn e do `entrypoint.sh` em tempo real. Troque por `nginx` ou `db` conforme o caso. |
| `docker compose logs --tail 200` | Últimas 200 linhas de **todos** os serviços — o primeiro comando a rodar quando algo não sobe. |
| `docker compose config` | Mostra a configuração final, já com o `.env` interpolado e os overrides aplicados. É como conferir o que o Compose realmente entendeu. |
| `docker compose config -q` | Valida o arquivo sem imprimir nada; sai com erro se houver problema (é o que a CI usa). |
| `docker compose exec web sh` | Shell dentro do contêiner da aplicação, já como usuário `app`. |
| `docker compose exec web python manage.py createsuperuser` | Cria um usuário para o `/admin/`. |
| `docker compose exec web python manage.py showmigrations` | Confere o estado das migrações aplicadas. |
| `docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'` | Abre o `psql` no banco, usando as credenciais já presentes no contêiner (as aspas simples impedem que o shell do host expanda as variáveis). |
| `docker compose restart nginx` | Reinicia só o proxy — suficiente depois de mudanças que não exijam rebuild. |
| `docker compose up -d --build nginx` | Reconstrói e recria o Nginx depois de editar `nginx/default.conf`. |
| `docker compose build --no-cache web` | Rebuild completo da imagem da aplicação, ignorando o cache de camadas. |
| `docker compose stop` | Para os contêineres sem removê-los. |
| `docker compose down` | Para e **remove** os contêineres e a rede. **Preserva os volumes** — é a metade do `down`+`up` que o projeto demonstra. |
| `docker compose down -v` | Remove também os volumes: **apaga banco, uploads e estáticos**. |
| `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm web` | Roda a suíte `pytest` contra o PostgreSQL do stack. |
| `docker compose top` | Processos rodando em cada contêiner (para conferir os três workers do Gunicorn). |
| `docker volume ls` | Lista os volumes; os deste projeto começam com `django-upload-stack_`. |

!!! danger "`docker compose down -v` destrói os dados"
    O `-v` remove os volumes nomeados junto com os contêineres: o banco, os uploads e os
    estáticos vão embora sem confirmação e sem recuperação. Use `docker compose down`
    (sem a flag) no dia a dia. O `-v` é apropriado apenas para começar do zero de
    propósito — é o que o job de CI faz no passo de *Tear down*, e é o que você deve rodar
    se quiser reproduzir a semeadura inicial dos volumes.

E a verificação rápida depois de subir o stack — o único ponto em que a sintaxe muda entre
os dois shells do Windows:

=== "PowerShell"

    ```powershell
    curl.exe -i http://localhost:8080/healthz/
    ```

=== "Git Bash"

    ```bash
    curl -i http://localhost:8080/healthz/
    ```

!!! warning "No PowerShell é `curl.exe`, com o `.exe`"
    No PowerShell, `curl` é um **alias** para o cmdlet `Invoke-WebRequest`, que não entende
    as opções do curl de verdade (`-i`, `-F`, `-fsS`...) e falha com erros sobre parâmetros
    desconhecidos. Escrever `curl.exe` força a execução do binário real, que acompanha o
    Windows 10/11.

A resposta esperada é `HTTP/1.1 200 OK` com o corpo
`{"status": "ok", "database": "ok", "vendor": "postgresql"}`. Para a verificação completa —
upload, download byte a byte, `down`+`up` e prova de que o arquivo continua lá — use
`bash scripts/smoke_test.sh`, descrito em [Persistência e volumes](persistencia.md).

Quando algum desses comandos não se comportar como descrito aqui, comece por
[Troubleshooting](troubleshooting.md): os sintomas estão organizados pela causa raiz
explicada nesta página.
