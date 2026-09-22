# Instalação e execução local

Este guia leva alguém que nunca viu o projeto do zero até a stack rodando em
`http://localhost:8080/`, com upload funcionando e dados sobrevivendo a um
`docker compose down` seguido de `up`.

Tudo roda em containers: você **não precisa** de Python, PostgreSQL ou Nginx instalados
na máquina para usar a aplicação. Python só entra em cena se você quiser rodar a suíte de
testes fora do Docker (seção [Rodando os testes](#rodando-os-testes)).

---

## 1. Pré-requisitos

| Ferramenta | O que é preciso | Por quê |
| --- | --- | --- |
| **Docker Desktop** | Engine 25 ou superior, com **Compose v2** | `docker compose up --wait` exige Compose v2, e a chave `start_interval` usada nos healthchecks do `docker-compose.yml` exige Engine 25+ |
| **Git** | qualquer versão recente | clonar o repositório; o `.gitattributes` garante que `entrypoint.sh`, `*.conf` e `.env*` cheguem com fim de linha **LF** |
| **Git Bash** (Windows) | já vem junto com o Git for Windows | necessário para `scripts/smoke_test.sh` |
| **Python 3.13 + uv** | opcional | apenas para rodar `pytest`/`ruff` fora do Docker |

Confira as versões (os três comandos são iguais no PowerShell e no Git Bash):

```bash
docker --version
docker compose version
git --version
```

!!! note "Compose v2 é `docker compose`, com espaço"
    Se `docker compose version` falhar mas `docker-compose --version` funcionar, você está
    no Compose v1 (Python), que não suporta `--wait` nem `start_interval`. Atualize o
    Docker Desktop — este projeto não é compatível com o v1.

!!! tip "Windows: use o backend WSL 2"
    O Docker Desktop com WSL 2 é bem mais rápido para builds e para I/O de volumes.
    Como a stack usa **volumes nomeados** (e não bind mounts do diretório do projeto),
    nada depende da pasta local — o que evita, de quebra, os problemas clássicos de
    sincronização quando o repositório está dentro de uma pasta do OneDrive.

---

## 2. Clonar o repositório

```bash
git clone https://github.com/lucasetculbra/django-upload-stack.git
cd django-upload-stack
```

Todos os comandos deste guia assumem que você está na **raiz do repositório** (a pasta que
contém `docker-compose.yml`). O Compose procura o arquivo `docker-compose.yml` e o `.env`
a partir do diretório atual.

---

## 3. Criar o arquivo `.env`

O `.env` é obrigatório: o serviço `web` o declara em `env_file: .env`, e o serviço `db`
depende dele para as variáveis `POSTGRES_DB`, `POSTGRES_USER` e `POSTGRES_PASSWORD`.
Sem o arquivo, o `docker compose up` falha antes de subir qualquer container.

O `.env` está no `.gitignore` (apenas o `.env.example` é versionado) e também no
`.dockerignore`, para nunca ser copiado para dentro de uma imagem.

### 3.1 Copiar o exemplo

=== "PowerShell"

    ```powershell
    Copy-Item .env.example .env
    ```

=== "Git Bash"

    ```bash
    cp .env.example .env
    ```

!!! warning "Copie o arquivo, não o conteúdo"
    Use `Copy-Item`/`cp`, que copiam os **bytes** do arquivo. Fazer
    `Get-Content .env.example | Set-Content .env` no PowerShell reescreve o arquivo com a
    codificação padrão do console (que pode ser ANSI ou UTF-16) e costuma quebrar a
    leitura pelo Docker Compose.

### 3.2 Gerar um `SECRET_KEY`

O `.env.example` vem com o valor placeholder
`SECRET_KEY=change-me-use-a-unique-random-value-in-your-own-env`. Ele **funciona**, mas é
público — troque antes de qualquer coisa. O `config/settings.py` só valida a presença da
chave:

```python
SECRET_KEY = env_str("SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY is required. Copy .env.example to .env and set a unique value "
        "(see docs/instalacao.md)."
    )
```

Os comandos abaixo geram uma chave aleatória e a gravam no `.env` já com a codificação
correta:

=== "PowerShell"

    ```powershell
    # Gera 48 bytes aleatórios em base64url (apenas A-Z a-z 0-9 - _), sem precisar de Python.
    $bytes = [byte[]]::new(48)
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $secret = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')

    # Reescreve a linha SECRET_KEY= gravando UTF-8 SEM BOM (o $false é exatamente isso).
    $envPath = Join-Path $PWD '.env'
    $content = (Get-Content $envPath -Raw) -replace '(?m)^SECRET_KEY=.*$', "SECRET_KEY=$secret"
    [System.IO.File]::WriteAllText($envPath, $content, (New-Object System.Text.UTF8Encoding($false)))
    ```

=== "Git Bash"

    ```bash
    # openssl acompanha o Git for Windows; a saída em hex só tem [0-9a-f].
    SECRET="$(openssl rand -hex 32)"
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET|" .env
    ```

    Com Python disponível, o one-liner sugerido no próprio `.env.example` também serve:

    ```bash
    SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET|" .env
    ```

!!! danger "Nunca use `get_random_secret_key()` para preencher este `.env`"
    `django.core.management.utils.get_random_secret_key()` sorteia caracteres do conjunto
    `abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)`, que inclui `$`, `#` e `%`.
    Uma chave assim quebra a interpolação do Compose (veja a seção seguinte).
    `secrets.token_urlsafe()` e `openssl rand -hex` produzem apenas caracteres seguros —
    por isso são eles que aparecem no `.env.example` e no workflow de CI.

### 3.3 Por que UTF-8 **sem BOM** e sem `$` nos valores

O cabeçalho do `.env.example` é explícito:

```ini
# Copy to .env and adjust. Values must not contain $, # or spaces: Docker Compose
# interpolates this file, so those characters would silently mangle the value.
```

Duas armadilhas específicas, e ambas falham **em silêncio** — sem mensagem de erro clara:

**BOM (Byte Order Mark).** No Windows PowerShell 5.1, `>`, `Out-File` e `Set-Content`
gravam, dependendo do caso, UTF-16 ou UTF-8 **com** BOM: três bytes invisíveis
(`EF BB BF`) no começo do arquivo. Esses bytes grudam no nome da primeira variável do
arquivo, que deixa de se chamar `SECRET_KEY` e passa a ser `﻿SECRET_KEY`. O Compose
repassa a variável com o nome errado, o Django não encontra `SECRET_KEY` e o container
`web` morre no boot com `ImproperlyConfigured: SECRET_KEY is required`. Um BOM no meio do
arquivo é impossível; o problema é sempre a primeira linha — por isso o `.env.example`
começa com comentários, o que atenua, mas não resolve, o caso de um arquivo reescrito.

**Cifrão.** O Compose faz **interpolação** no `.env`: `$ALGO` e `${ALGO}` são substituídos
pelo valor da variável de ambiente correspondente. Se o seu `SECRET_KEY` contiver, por
exemplo, `ab$cde`, o Compose tenta expandir `$cde`, não encontra nada e usa string vazia —
sua chave chega truncada ao container, avisando apenas com um
`WARN[0000] The "cde" variable is not set. Defaulting to a blank string.` perdido no meio
do log. O mesmo vale para `#`, que inicia comentário, e para espaços.

Verifique se sobrou BOM no seu `.env`:

=== "PowerShell"

    ```powershell
    [System.IO.File]::ReadAllBytes((Join-Path $PWD '.env'))[0..2]
    # 239 187 191  -> tem BOM (EF BB BF): regrave o arquivo sem BOM
    # 35 32 67     -> começa com "# C": está correto
    ```

=== "Git Bash"

    ```bash
    head -c 3 .env | od -An -tx1
    # ef bb bf  -> tem BOM
    # 23 20 43  -> começa com "# C": está correto
    ```

!!! tip "Mantenha o fim de linha LF"
    O `.gitattributes` declara `.env* text eol=lf`, então o `.env.example` chega ao disco
    com LF. Se um editor salvar o `.env` em CRLF, o `\r` pode entrar no fim de cada valor
    (um `ALLOWED_HOSTS` terminando em `web\r`, por exemplo) e causar falhas difíceis de
    enxergar. No VS Code, o indicador de fim de linha fica no canto inferior direito.

### 3.4 Validar antes de subir

```bash
docker compose config -q
```

Sem saída = arquivo válido. Esse comando resolve o `docker-compose.yml` inteiro, aplica a
interpolação do `.env` e é exatamente o que a CI executa antes de subir a stack. Se algum
valor tiver `$`, o aviso sobre "variable is not set" aparece aqui.

Confirme também que o placeholder foi mesmo substituído:

=== "PowerShell"

    ```powershell
    Select-String -Path .env -Pattern '^SECRET_KEY=change-me'
    # sem resultado = ok
    ```

=== "Git Bash"

    ```bash
    grep -c '^SECRET_KEY=change-me' .env
    # 0 = ok
    ```

O detalhamento de cada variável (`DEBUG`, `ALLOWED_HOSTS`, `MAX_UPLOAD_MB`,
`LOG_LEVEL`, `SECURE_*` etc.) está em [Variáveis de ambiente](variaveis.md).

---

## 4. Subir a stack

```bash
docker compose up -d --build --wait
```

O que cada parte faz:

- **`--build`** constrói as duas imagens locais: `web` (estágio `runtime` do `Dockerfile`)
  e `nginx` (a partir de `nginx/Dockerfile`, que copia o `default.conf` para dentro da
  imagem). Na primeira execução isso baixa `python:3.13-slim`, `postgres:17-alpine` e
  `nginx:1.30-alpine` e instala as dependências do `requirements.txt` — é o passo
  demorado; as próximas vezes aproveitam o cache.
- **`-d`** roda em segundo plano.
- **`--wait`** é o pulo do gato: o comando só retorna quando todos os healthchecks
  estiverem `healthy`. Como o `nginx` depende de `web: service_healthy` e o `web` depende
  de `db: service_healthy`, quando o prompt volta a cadeia inteira já respondeu. Sem
  `--wait`, o comando retorna enquanto o Nginx ainda está subindo e a primeira requisição
  pode falhar com "empty reply from server".

Se o build for lento na sua máquina, aumente o limite de espera (é o valor que a CI usa):

```bash
docker compose up -d --build --wait --wait-timeout 300
```

Nas execuções seguintes, quando nada mudou no código, `--build` é dispensável:

```bash
docker compose up -d --wait
```

---

## 5. Conferir status e logs

```bash
docker compose ps
```

Os três containers têm nomes fixos:

| Serviço | Container | Estado esperado | Portas |
| --- | --- | --- | --- |
| `db` | `dus-db` | `Up (healthy)` | nenhuma publicada |
| `web` | `dus-web` | `Up (healthy)` | `8000` apenas na rede interna (`expose`) |
| `nginx` | `dus-nginx` | `Up (healthy)` | `0.0.0.0:8080->80/tcp` |

!!! note "Só o Nginx tem porta publicada"
    Essa é uma decisão de arquitetura, não um descuido: Gunicorn e PostgreSQL ficam
    acessíveis apenas pela rede `backend` do Compose. Detalhes em
    [Arquitetura](arquitetura.md).

Logs:

```bash
docker compose logs -f web      # acompanha em tempo real
docker compose logs --tail 50 nginx
docker compose logs db
docker compose logs --tail 200  # todos os serviços de uma vez
```

No primeiro boot, o `entrypoint.sh` do container `web` imprime exatamente esta sequência
antes do Gunicorn assumir:

```text
[entrypoint] waiting for postgres at db:5432 ...
[entrypoint] database is ready
[entrypoint] applying migrations
[entrypoint] collecting static files
[entrypoint] starting: gunicorn config.wsgi:application --bind 0.0.0.0:8000 ...
```

Ou seja: migrações e `collectstatic` rodam sozinhos a cada start — você não precisa
executá-los à mão.

---

## 6. URLs que funcionam

| URL | O que é |
| --- | --- |
| <http://localhost:8080/> | Formulário de upload e listagem dos arquivos enviados |
| <http://localhost:8080/healthz/> | Probe em JSON usada pelos healthchecks e pelo smoke test |
| <http://localhost:8080/admin/> | Django admin (precisa de superusuário — seção 7) |
| `http://localhost:8080/media/uploads/AAAA/MM/...` | Arquivos enviados, servidos direto pelo Nginx a partir do volume `media_data` |
| `http://localhost:8080/static/...` | Estáticos gerados pelo `collectstatic` no volume `static_data` |

Teste o endpoint de saúde pela linha de comando:

=== "PowerShell"

    ```powershell
    curl.exe -s http://localhost:8080/healthz/
    ```

=== "Git Bash"

    ```bash
    curl -s http://localhost:8080/healthz/
    ```

Resposta esperada quando tudo está no ar:

```json
{"status": "ok", "database": "ok", "vendor": "postgresql"}
```

!!! warning "No PowerShell é `curl.exe`, com o `.exe`"
    No PowerShell, `curl` é um **alias** para `Invoke-WebRequest`, que não entende as
    flags do curl de verdade (`-s`, `-F`, `-o`, `-w`...) e falha com erro de parâmetro.
    Escrever `curl.exe` força o binário real, que vem junto com o Windows 10/11.
    O `"vendor": "postgresql"` na resposta é a prova de que o Django está falando com o
    PostgreSQL, e não com um SQLite de fallback.

---

## 7. Criar um superusuário

Para entrar em `/admin/`:

```bash
docker compose exec web python manage.py createsuperuser
```

O comando é interativo (pede usuário, e-mail e senha duas vezes) e precisa de um terminal
de verdade — funciona tanto no PowerShell quanto no Git Bash. Não é preciso definir
variáveis de ambiente: o `docker compose exec` entra no container `dus-web`, que já tem
`DJANGO_SETTINGS_MODULE=config.settings` e as credenciais do banco.

!!! tip "Versão não interativa (útil em scripts)"
    O `createsuperuser` do Django aceita `--noinput` lendo a senha de
    `DJANGO_SUPERUSER_PASSWORD`:

    ```bash
    docker compose exec -e DJANGO_SUPERUSER_PASSWORD=troque-esta-senha web \
        python manage.py createsuperuser --noinput \
        --username admin --email admin@example.com
    ```

O usuário criado vive na tabela `auth_user` dentro do volume `postgres_data`: ele
**sobrevive** a `docker compose down` + `up`, e some com `docker compose down -v`.

---

## 8. Mudar a porta publicada (`NGINX_PORT`)

A porta do host é interpolada no `docker-compose.yml` como `"${NGINX_PORT:-8080}:80"` —
ou seja, 8080 é só o padrão. Para usar 9000 (por exemplo, porque algo já ocupa a 8080),
edite **duas** linhas do `.env`:

```ini
CSRF_TRUSTED_ORIGINS=http://localhost:9000,http://127.0.0.1:9000
NGINX_PORT=9000
```

E recrie o container do Nginx (o Compose detecta a mudança de portas sozinho):

```bash
docker compose up -d --wait
```

!!! danger "Trocar a porta sem ajustar CSRF_TRUSTED_ORIGINS quebra o upload"
    O `nginx/default.conf` repassa `proxy_set_header Host $http_host`, preservando a porta
    (`localhost:9000`). O Django, então, compara o cabeçalho `Origin` da requisição com
    `CSRF_TRUSTED_ORIGINS` — e `http://localhost:9000` não está lá se você só mexeu no
    `NGINX_PORT`. Resultado: o `GET /` funciona normalmente, mas todo `POST` do formulário
    volta **403 Forbidden** com "Origin checking failed". Os dois valores precisam andar
    juntos.

!!! note "E o ALLOWED_HOSTS?"
    Não precisa mudar. O Django valida `ALLOWED_HOSTS` apenas pelo nome do host,
    descartando a porta — `localhost` continua válido em qualquer porta. Já o
    `CSRF_TRUSTED_ORIGINS` exige a origem completa, com esquema **e** porta.

---

## 9. Parar, reiniciar e limpar

| Comando | O que acontece com os dados |
| --- | --- |
| `docker compose stop` | Containers param; nada é removido |
| `docker compose down` | Remove containers e a rede. **Volumes permanecem**: uploads, banco e estáticos continuam lá |
| `docker compose down -v` | Remove também os volumes nomeados: **apaga uploads, banco e usuários** |
| `docker compose down --rmi local` | Além dos containers, remove as imagens construídas localmente |
| `docker compose restart web` | Reinicia só o `web` (o `entrypoint.sh` roda de novo: migra e coleta estáticos) |

O ciclo que este projeto existe para demonstrar:

```bash
docker compose down
docker compose up -d --wait
```

Depois disso, recarregue <http://localhost:8080/>: os arquivos enviados continuam na
listagem e continuam baixáveis. Os containers são descartáveis; os volumes nomeados
`postgres_data`, `media_data` e `static_data` é que guardam o estado. O porquê disso está
em [Persistência e volumes](persistencia.md).

!!! danger "`down -v` é destrutivo e não tem desfazer"
    A flag `-v` remove os volumes nomeados do projeto — inclusive `media_data`
    (todos os arquivos enviados) e `postgres_data` (banco inteiro, superusuários
    incluídos). Use apenas quando quiser mesmo começar do zero.

Para ver os volumes (o prefixo vem da chave `name: django-upload-stack` no
`docker-compose.yml`):

```bash
docker volume ls --filter name=django-upload-stack
```

```text
django-upload-stack_media_data
django-upload-stack_postgres_data
django-upload-stack_static_data
```

---

## Rodando os testes

A suíte fica em `uploads/tests/` e é configurada pelo `[tool.pytest.ini_options]` do
`pyproject.toml`:

```ini
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings_test"
python_files = ["test_*.py"]
testpaths = ["uploads/tests"]
addopts = "-ra"
```

Há três formas de rodá-la, com propósitos diferentes:

| Forma | Banco | Precisa de Docker? | Para quê |
| --- | --- | --- | --- |
| `pytest` local | SQLite | não | feedback rápido enquanto você edita código |
| Overlay `docker-compose.test.yml` | PostgreSQL | sim | garantir que o código funciona no banco de verdade |
| `scripts/smoke_test.sh` | PostgreSQL | sim | validar a stack inteira, incluindo persistência |

### A. Local, com uv ou pip (SQLite, zero configuração)

```bash
uv venv --python 3.13
```

Ative o ambiente:

=== "PowerShell"

    ```powershell
    .\.venv\Scripts\Activate.ps1
    uv pip install -r requirements-dev.txt
    pytest
    ```

=== "Git Bash"

    ```bash
    source .venv/Scripts/activate
    uv pip install -r requirements-dev.txt
    pytest
    ```

Sem `uv`, o equivalente é `python -m venv .venv` e `pip install -r requirements-dev.txt`
(o `requirements-dev.txt` já inclui o `requirements.txt` via `-r`, e traz `pytest`,
`pytest-django` e `ruff`).

Para reproduzir o job de lint da CI:

```bash
ruff check .
ruff format --check .
```

**Por que funciona sem configurar nada.** O módulo `config/settings_test.py` define
padrões seguros *antes* de importar as settings reais:

```python
import os

os.environ.setdefault("DJANGO_DB", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-used-in-production")
os.environ.setdefault("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver,web")

from config.settings import *  # noqa: E402, F403
```

Três consequências importantes:

1. `DJANGO_DB=sqlite` evita a exigência das variáveis `POSTGRES_*` — em
   `config/settings.py`, `DJANGO_DB=postgres` levanta `ImproperlyConfigured` se
   `POSTGRES_DB`, `POSTGRES_USER` ou `POSTGRES_PASSWORD` estiverem faltando. O SQLite
   nunca é um *fallback* automático: ele precisa ser pedido explicitamente, justamente
   para que ninguém rode a aplicação em produção gravando dados na camada efêmera do
   container.
2. O `SECRET_KEY` de teste satisfaz a validação do `settings.py` sem nenhum `.env` —
   nada no código Python lê o arquivo `.env`; ele só existe para o Docker Compose.
3. `setdefault` **não sobrescreve** variáveis já existentes no ambiente. É isso que
   permite que a *mesma* suíte rode contra PostgreSQL bastando exportar `DJANGO_DB` e as
   `POSTGRES_*` — é assim que a CI e o overlay de teste funcionam.

O banco de teste é criado e destruído pelo `pytest-django` (com SQLite, em memória por
padrão), então o `db.sqlite3` do diretório do projeto não é tocado. As fixtures de
`uploads/tests/test_views.py` ainda apontam `MEDIA_ROOT` para um `tmp_path`, de modo que
nenhum teste escreve na pasta real de mídia.

!!! warning "Variável de ambiente sobrando"
    Por causa do `setdefault`, se a sua sessão de terminal ainda tiver `DJANGO_DB=postgres`
    exportado (de um experimento anterior), o `pytest` vai tentar conectar no PostgreSQL e
    falhar. Limpe antes:

    === "PowerShell"

        ```powershell
        Remove-Item Env:DJANGO_DB -ErrorAction SilentlyContinue
        ```

    === "Git Bash"

        ```bash
        unset DJANGO_DB
        ```

### B. Dentro da stack, contra o PostgreSQL

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm web
```

Adicione `--build` depois de mexer no `requirements-dev.txt` ou no `Dockerfile`:

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build web
```

O overlay `docker-compose.test.yml` é curto e cada linha tem um motivo:

```yaml
services:
  web:
    build:
      target: test          # estágio 3 do Dockerfile: runtime + pytest/ruff
    image: django-upload-stack-web:test
    entrypoint: []          # descarta o entrypoint.sh (nada de migrate/collectstatic)
    command: ["pytest"]
    restart: "no"
    healthcheck:
      disable: true         # um container de teste não precisa ficar "healthy"
```

Como o `depends_on: db: condition: service_healthy` do arquivo base continua valendo, o
`run` sobe o PostgreSQL e espera o healthcheck passar antes de executar o `pytest`.
O `DJANGO_DB=postgres` vem do seu `.env` (via `env_file`) e o `POSTGRES_HOST=db` vem do
bloco `environment:` do serviço — então o `setdefault` do `settings_test.py` não age, e a
suíte roda contra o banco real. O `pytest-django` cria e derruba um banco `test_...`
separado, sem encostar nos dados de desenvolvimento.

!!! note "O container do banco continua no ar"
    `run --rm` remove apenas o container de teste; o `dus-db` que ele iniciou permanece
    rodando. Encerre com `docker compose down` quando terminar (sem `-v`, para preservar
    os dados).

### C. Smoke test ponta a ponta

É o teste que valida o critério de aceitação do projeto: upload real, servido pelo Nginx,
sobrevivendo à recriação da stack.

```bash
docker compose up -d --build --wait
bash scripts/smoke_test.sh
```

Se você mudou a porta, passe a URL base (o script também aceita a variável `BASE_URL`):

```bash
bash scripts/smoke_test.sh http://localhost:9000
```

As seis etapas, direto do script:

| Etapa | Verificação |
| --- | --- |
| 1/6 | `GET /` responde 2xx e `/healthz/` retorna `"database": "ok"` |
| 2/6 | `POST` de upload **sem** token CSRF é rejeitado com 403 |
| 3/6 | `POST` com cookie `csrftoken`, `Origin` e `Referer` retorna 302 |
| 4/6 | O arquivo aparece na listagem e o Nginx o devolve **byte a byte** idêntico (`cmp`) |
| 5/6 | `docker compose down` seguido de `up -d --wait --wait-timeout 300` |
| 6/6 | O registro continua listado (volume do PostgreSQL) e o arquivo continua íntegro (volume de mídia) |

!!! danger "Este script derruba e recria a stack"
    A etapa 5 executa `docker compose down` e sobe tudo de novo — é o ponto inteiro do
    teste. Não rode com algo importante no ar. Ele **não** usa `-v`, então os volumes (e
    portanto os dados) são preservados de propósito. Ao final, fica um arquivo
    `smoke-AAAAMMDD-HHMMSS-PID.bin` na listagem, criado pelo próprio teste.

!!! warning "No Windows, rode pelo Git Bash — não pelo PowerShell"
    O `smoke_test.sh` é Bash e usa `/dev/urandom`, `mktemp -d`, `trap`, `awk`, `cmp` e
    `head -c`. No PowerShell, `bash scripts/smoke_test.sh` só funciona se `bash` estiver
    no `PATH` (WSL ou Git), e mesmo assim o caminho pode ser traduzido de forma
    inesperada. Abra o **Git Bash** na raiz do repositório e rode a partir de lá.

!!! note "Rode a partir da raiz do repositório"
    O script muda para um diretório temporário para gerar o arquivo de teste e volta com
    `cd -` antes de chamar `docker compose down`/`up`. Se você o invocar de outro
    diretório, esses comandos não vão encontrar o `docker-compose.yml`.

Esse mesmo script é executado pelo job `smoke` do workflow de CI — veja
[CI/CD](ci-cd.md).

---

## Usando a imagem publicada no GHCR

Cada push na `main` publica a imagem da aplicação em
`ghcr.io/lucasetculbra/django-upload-stack` (estágio `runtime`, linux/amd64). Dá para usar
essa imagem em vez de construir localmente, porque o `docker-compose.yml` declara:

```yaml
image: ${WEB_IMAGE:-django-upload-stack-web:local}
```

Tags disponíveis:

| Tag | Origem |
| --- | --- |
| `latest` | último push na branch padrão |
| `main` | mesma coisa, nomeada pela branch |
| `sha-<commit>` | commit específico, ideal para reproduzir um estado exato |
| `1.2.3` / `1.2` | push de uma tag `v1.2.3` |

=== "PowerShell"

    ```powershell
    $env:WEB_IMAGE = "ghcr.io/lucasetculbra/django-upload-stack:latest"
    docker compose pull web
    docker compose up -d --wait
    ```

=== "Git Bash"

    ```bash
    export WEB_IMAGE=ghcr.io/lucasetculbra/django-upload-stack:latest
    docker compose pull web
    docker compose up -d --wait
    ```

Para fixar a escolha, basta acrescentar a linha ao `.env` (a variável não está no
`.env.example` justamente por ser opcional):

```ini
WEB_IMAGE=ghcr.io/lucasetculbra/django-upload-stack:latest
```

!!! warning "Não passe `--build` ao usar a imagem do GHCR"
    O serviço `web` tem seção `build:` **e** `image:`. Com `--build`, o Compose
    reconstrói localmente e aplica a tag do GHCR à sua imagem local, apagando a diferença
    entre as duas. Suba sem `--build` depois do `pull`.

Dois detalhes:

- O **Nginx sempre é construído localmente** a partir de `nginx/Dockerfile`; não existe
  imagem publicada para ele. A primeira subida ainda precisa construir essa imagem.
- A imagem do GHCR contém apenas o estágio `runtime`, sem `pytest`. O overlay
  `docker-compose.test.yml` continua construindo o estágio `test` na sua máquina.
- Se o `pull` falhar com `denied`, autentique-se com
  `docker login ghcr.io -u SEU_USUARIO` usando um Personal Access Token com escopo
  `read:packages`.

---

## Próximos passos

- [Arquitetura](arquitetura.md) — o caminho de uma requisição e por que só o Nginx publica porta.
- [Docker Compose e Dockerfile](docker-compose.md) — cada serviço, estágio e healthcheck em detalhe.
- [Persistência e volumes](persistencia.md) — o que exatamente sobrevive a um `down`/`up`.
- [Variáveis de ambiente](variaveis.md) — referência completa do `.env`.
- [Troubleshooting](troubleshooting.md) — 403 no upload, 502 do Nginx, admin sem CSS, porta ocupada.
