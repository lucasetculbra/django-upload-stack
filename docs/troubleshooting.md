# Troubleshooting

Catálogo dos problemas que realmente acontecem nesta stack, na ordem em que costumam
aparecer. Cada item traz o **sintoma** (a mensagem literal), a **causa** e a **solução**
com os comandos exatos.

!!! tip "Comece sempre por aqui"

    ```bash
    docker compose ps -a          # quem está de pé, quem está unhealthy, quem saiu
    docker compose logs --tail 50 # o erro quase sempre está nas últimas linhas
    docker compose config         # como o Compose interpretou o .env
    ```

---

## A porta 8080 já está em uso

**Sintoma**

```text
Error response from daemon: Ports are not available: exposing port TCP 0.0.0.0:8080 -> 0.0.0.0:0:
listen tcp 0.0.0.0:8080: bind: Only one usage of each socket address ... is normally permitted.
```

ou `Bind for 0.0.0.0:8080 failed: port is already allocated`.

**Causa** — outro processo já escuta na 8080. No Windows, os suspeitos frequentes são um
servidor rodando dentro do WSL (o listener aparece como `wslrelay.exe`), outro projeto
Docker, ou um intervalo de portas reservado pelo Hyper-V.

**Solução** — descubra quem é o dono da porta:

=== "PowerShell"

    ```powershell
    Get-NetTCPConnection -LocalPort 8080 -State Listen |
        ForEach-Object { Get-Process -Id $_.OwningProcess } |
        Select-Object Id, ProcessName, Path
    ```

=== "Git Bash"

    ```bash
    netstat -ano | grep LISTENING | grep :8080
    ```

Se for um serviço dentro do WSL:

```powershell
wsl -d <distro> -- bash -c "ss -ltnp | grep :8080"
wsl -d <distro> -u root -- systemctl disable --now nginx   # exemplo
```

Se a porta não estiver em uso por ninguém e o bind ainda falhar, ela pode estar em um
intervalo reservado pelo Hyper-V/WSL:

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

A alternativa mais simples é **trocar a porta publicada** no `.env`:

```ini
NGINX_PORT=8081
CSRF_TRUSTED_ORIGINS=http://localhost:8081,http://127.0.0.1:8081
```

```bash
docker compose up -d
```

!!! warning "Não esqueça do CSRF"

    Ao trocar `NGINX_PORT` você **precisa** atualizar `CSRF_TRUSTED_ORIGINS` com a nova
    porta, senão todo upload pelo navegador passa a falhar com 403. Veja
    [Variáveis de ambiente](variaveis.md).

---

## `SECRET_KEY is required` / ImproperlyConfigured

**Sintoma** — o container `web` sobe e morre; `docker compose logs web` mostra:

```text
django.core.exceptions.ImproperlyConfigured: SECRET_KEY is required.
Copy .env.example to .env and set a unique value (see docs/instalacao.md).
```

**Causas possíveis**

1. O arquivo `.env` não existe (ele é ignorado pelo Git de propósito).
2. O `.env` foi criado no PowerShell com `>` ou `Out-File` e ficou com **BOM**: a primeira
   variável vira `\ufeffSECRET_KEY` e o Django não a enxerga.
3. O valor contém `$`, `#` ou espaço: o Compose **interpola** o `.env`, então `$` inicia uma
   substituição de variável e ` #` inicia um comentário — o valor chega truncado ou vazio.

**Diagnóstico** — veja o que o Compose realmente entendeu:

```bash
docker compose config | grep SECRET_KEY
```

Procure um BOM nos primeiros bytes (não deve começar com `ef bb bf`):

```bash
head -c 16 .env | od -An -tx1
```

**Solução** — recrie o `.env` com UTF-8 **sem BOM** e uma chave segura (apenas
`[A-Za-z0-9_-]`):

=== "PowerShell"

    ```powershell
    $key = python -c "import secrets; print(secrets.token_urlsafe(50))"
    $content = (Get-Content .env.example) -replace '^SECRET_KEY=.*', "SECRET_KEY=$key"
    [IO.File]::WriteAllLines("$PWD\.env", $content, [Text.UTF8Encoding]::new($false))
    ```

=== "Git Bash"

    ```bash
    cp .env.example .env
    KEY=$(python -c "import secrets; print(secrets.token_urlsafe(50))")
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$KEY|" .env
    ```

Confira também variáveis com `\r` no fim (arquivo salvo com CRLF):

```bash
docker compose run --rm --no-deps web env | cat -A | grep -E 'SECRET_KEY|ALLOWED'
```

Um `^M$` no fim da linha indica CRLF — o `.gitattributes` do projeto força LF em `.env*`,
mas um arquivo criado manualmente pode escapar disso.

---

## HTTP 400 "Bad Request" e o container que nunca fica *healthy*

**Sintoma** — `docker compose up --wait` fica parado e depois falha; `docker compose ps`
mostra `web` como `(health: starting)` e depois `(unhealthy)`. Nos logs:

```text
Invalid HTTP_HOST header: '127.0.0.1:8000'. You may need to add '127.0.0.1' to ALLOWED_HOSTS.
```

**Causa** — `ALLOWED_HOSTS` não inclui o host usado na requisição. Com `DEBUG=0` o Django
responde 400 para qualquer host desconhecido, o healthcheck nunca passa, e como o `nginx`
depende de `web: service_healthy`, a stack inteira não sobe.

**Solução** — garanta os três hosts no `.env`:

```ini
ALLOWED_HOSTS=localhost,127.0.0.1,web
```

```bash
docker compose up -d --force-recreate web
docker compose logs -f web
```

!!! note "Por que `web` está na lista"

    `web` é o nome do serviço na rede do Compose. O `nginx` encaminha o `Host` original do
    cliente, mas ferramentas internas (e um `curl` de dentro de outro container) usam
    `http://web:8000` — sem essa entrada, essas chamadas retornariam 400.

---

## HTTP 403 no upload: "CSRF verification failed" / "Origin checking failed"

**Sintoma** — o formulário abre normalmente, mas ao enviar o arquivo o navegador recebe:

```text
Forbidden (403)
CSRF verification failed. Request aborted.
Origin checking failed - http://localhost:8080 does not match any trusted origins.
```

**Causa** — o Django compara o header `Origin` enviado pelo navegador com
`CSRF_TRUSTED_ORIGINS` (e com o host da requisição). Dois detalhes derrubam isso:

1. `CSRF_TRUSTED_ORIGINS` não contém a origem **com esquema e porta exatos**.
2. O proxy repassa o `Host` sem a porta. Por isso o `nginx/default.conf` usa
   `proxy_set_header Host $http_host` e **não** `$host` — `$host` descarta a porta e faz o
   Django calcular a origem como `http://localhost`, que nunca casa com
   `http://localhost:8080`.

**Solução**

```ini
# .env - use exatamente a porta publicada
CSRF_TRUSTED_ORIGINS=http://localhost:8080,http://127.0.0.1:8080
```

```bash
docker compose up -d --force-recreate web
```

Se você alterou o `nginx/default.conf`, confirme a diretiva e reconstrua a imagem do proxy
(a configuração é embutida na imagem, não montada por bind mount):

```bash
docker compose exec nginx grep -n 'proxy_set_header Host' /etc/nginx/conf.d/default.conf
docker compose up -d --build nginx
```

!!! info "Testando com `curl`"

    O `curl` não envia `Origin` por padrão, então um upload via `curl` pode passar enquanto
    o navegador falha. O `scripts/smoke_test.sh` envia `Origin` e `Referer` justamente para
    reproduzir o comportamento real do navegador.

---

## HTTP 502 Bad Gateway

**Sintoma** — a página `/` retorna 502; `docker compose logs nginx` mostra:

```text
connect() failed (111: Connection refused) while connecting to upstream, upstream: "http://172.18.0.3:8000/"
```

**Causas e soluções**

| Causa | Como confirmar | Solução |
|---|---|---|
| O `web` ainda está subindo (`migrate` + `collectstatic` rodam antes do Gunicorn) | `docker compose ps` mostra `health: starting` | aguarde; use `docker compose up -d --wait` |
| O `web` caiu (erro de configuração, exceção na inicialização) | `docker compose logs web` | corrija o erro e `docker compose up -d --force-recreate web` |
| O `web` foi recriado e ganhou um IP novo | o IP do log não bate com `docker compose exec nginx getent hosts web` | já mitigado: o `default.conf` usa `resolver 127.0.0.11` + `proxy_pass $upstream`, que resolve o nome a cada requisição. Se você editou essa parte, restaure-a ou reinicie o Nginx |

```bash
docker compose restart nginx        # último recurso
docker compose logs --tail 50 web nginx
```

---

## HTTP 413 "Request Entity Too Large"

**Sintoma** — arquivos grandes falham com 413 e a requisição nem chega ao Django.

**Causa** — o `client_max_body_size` do Nginx (100 MB por padrão) é menor que o arquivo.

**Solução** — aumente **os dois lados** e mantenha-os em sincronia:

1. `nginx/default.conf`:

    ```nginx
    client_max_body_size 250M;
    ```

2. `.env`:

    ```ini
    MAX_UPLOAD_MB=250
    ```

3. Reconstrua a imagem do Nginx (a config está embutida) e recrie o `web`:

    ```bash
    docker compose up -d --build nginx web
    ```

!!! note "Quem recusa o quê"

    O Nginx corta a requisição **antes** do Django (413, página de erro do Nginx). Se o
    arquivo passa pelo Nginx mas excede `MAX_UPLOAD_MB`, quem recusa é o formulário do
    Django, com a mensagem "O arquivo excede o limite de N MB" e HTTP 200 na própria página.

---

## Uploads retornam 404 em `/media/` ou o site fica sem CSS

**Sintoma** — o arquivo aparece na listagem, mas o link `/media/...` devolve 404 do Nginx;
ou a página carrega sem estilo e `/static/uploads/style.css` devolve 404.

**Causas e soluções**

1. **O volume foi apagado** (`docker compose down -v`) e o arquivo antigo não existe mais,
   embora o registro no banco também tenha sumido junto. Se apenas um dos dois sumiu,
   confira os volumes:

    ```bash
    docker volume ls | grep django-upload-stack
    docker run --rm -v django-upload-stack_media_data:/v alpine ls -laR /v | head -20
    ```

2. **O `collectstatic` não rodou** — ele é executado pelo `entrypoint.sh` a cada boot do
   container. Verifique:

    ```bash
    docker compose logs web | grep collectstatic
    docker compose exec nginx ls /vol/static | head
    ```

3. **Permissão negada** (`13: Permission denied` no log do Nginx): os arquivos precisam ser
   legíveis pelo usuário `nginx`. O esperado é:

    ```bash
    docker compose exec web ls -ld /app/media
    # drwxr-xr-x 3 app app ... /app/media
    docker compose exec web ls -l /app/media/uploads/*/*/ | head -3
    # -rw-r--r-- 1 app app ...
    ```

    Se aparecer `root root` com permissões restritas, o volume foi inicializado pelo
    container errado. Corrija recriando os volumes de mídia/estáticos (isto **apaga** os
    arquivos já enviados):

    ```bash
    docker compose down
    docker volume rm django-upload-stack_media_data django-upload-stack_static_data
    docker compose up -d --wait
    ```

---

## Erro de autenticação no banco depois de trocar a senha

**Sintoma**

```text
psycopg.OperationalError: connection failed: FATAL:  password authentication failed for user "upload_stack"
```

logo após alterar `POSTGRES_PASSWORD` no `.env`.

**Causa** — o `initdb` da imagem oficial do PostgreSQL só roda quando o volume de dados
está **vazio**. Como `postgres_data` já existe, a senha antiga continua valendo; apenas a
aplicação passou a usar a nova.

**Solução A — manter os dados** (altere a senha dentro do banco):

```bash
docker compose exec db psql -U upload_stack -d upload_stack \
  -c "ALTER USER upload_stack WITH PASSWORD 'nova-senha';"
docker compose up -d --force-recreate web
```

**Solução B — recomeçar do zero** (apaga o banco **e** os uploads):

```bash
docker compose down -v
docker compose up -d --build --wait
```

---

## Mudanças no código não aparecem

**Sintoma** — você editou um `.py` ou um template e nada mudou no navegador.

**Causa** — `docker compose up -d` **não reconstrói** a imagem; o código é copiado para
dentro dela no build.

**Solução**

```bash
docker compose up -d --build
```

Para desenvolvimento com recarga automática, monte o código como volume e use o
`runserver` — mas lembre-se de que a stack é propositalmente de produção (Gunicorn, sem
autoreload).

---

## `permission denied` ou `$'\r': command not found` no entrypoint

**Sintoma**

```text
exec /app/entrypoint.sh: permission denied
```

ou

```text
/app/entrypoint.sh: line 2: $'\r': command not found
```

**Causa** — o repositório é escrito no Windows: o Git não preserva o bit de execução
(`core.filemode=false`) e pode gravar finais de linha CRLF, que o `/bin/sh` não entende.

**Solução** — o projeto já se protege em três camadas; se você adicionou um script novo,
replique-as:

1. `.gitattributes` força LF:

    ```gitattributes
    *.sh text eol=lf
    ```

2. O `Dockerfile` normaliza e marca como executável:

    ```dockerfile
    RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod 0755 /app/entrypoint.sh
    ```

3. O bit de execução é registrado no índice do Git:

    ```bash
    git update-index --chmod=+x entrypoint.sh scripts/smoke_test.sh
    ```

No CI, chame o script como `bash scripts/smoke_test.sh` (e não `./scripts/smoke_test.sh`)
para não depender do bit de execução.

---

## Particularidades do Windows

### `curl` no PowerShell não é o `curl`

No PowerShell, `curl` é um alias de `Invoke-WebRequest` e não aceita `-F`, `-w`, `-c`.
Use sempre `curl.exe`:

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:8080/
```

### O `smoke_test.sh` precisa do Git Bash

```bash
# no Git Bash, a partir da raiz do projeto
bash scripts/smoke_test.sh
```

Ele derruba e sobe a stack para provar a persistência — não rode com dados que você não
pode perder de vista (os dados **não** são apagados, mas os containers são recriados).

### O projeto dentro do OneDrive

Sincronização pode travar arquivos durante um `git rebase` ou deixar o *build context*
lento. Se aparecer `unable to write new index file` ou o build demorar muito:

- marque a pasta como **"Sempre manter neste dispositivo"**;
- ou pause a sincronização enquanto trabalha;
- ou mova o repositório para fora do OneDrive (ex.: `C:\dev\django-upload-stack`).

### Onde ficam os volumes

No Docker Desktop os volumes vivem dentro da VM do WSL, **não** são navegáveis pelo
Explorer. Para inspecioná-los:

```bash
docker run --rm -v django-upload-stack_media_data:/v alpine ls -laR /v
```

---

## Comandos de diagnóstico

| Objetivo | Comando |
|---|---|
| Status de todos os containers | `docker compose ps -a` |
| Logs ao vivo de um serviço | `docker compose logs -f web` |
| Últimas 100 linhas de tudo | `docker compose logs --tail 100` |
| Variáveis vistas pelo container | `docker compose exec web env \| sort` |
| Shell do Django | `docker compose exec web python manage.py shell` |
| Tabelas do banco | `docker compose exec db psql -U upload_stack -d upload_stack -c "\dt"` |
| Registros de upload | `docker compose exec db psql -U upload_stack -d upload_stack -c "SELECT id, original_name, size FROM uploads_uploadedfile ORDER BY id DESC LIMIT 5;"` |
| Conteúdo do volume de mídia | `docker run --rm -v django-upload-stack_media_data:/v alpine ls -laR /v` |
| Healthcheck da aplicação | `curl.exe http://localhost:8080/healthz/` |
| Config final do Compose | `docker compose config` |
| Testar a config do Nginx | `docker compose exec nginx nginx -t` |

---

## Reset completo

!!! danger "Isto apaga TODOS os dados"

    O comando abaixo remove os volumes: **todos os arquivos enviados e todo o banco de
    dados são perdidos**, sem possibilidade de recuperação. Faça um backup antes (veja
    [Persistência e volumes](persistencia.md)).

```bash
# 1. derruba tudo e apaga os volumes
docker compose down -v

# 2. (opcional) remove as imagens construídas localmente
docker image rm django-upload-stack-web:local django-upload-stack-nginx:local

# 3. recria o .env, se necessário
cp .env.example .env   # e gere uma nova SECRET_KEY

# 4. sobe do zero
docker compose up -d --build --wait
```

---

## Ainda não resolveu?

1. Releia os logs completos: `docker compose logs --no-color > logs.txt`.
2. Confirme que a configuração interpretada é a que você espera: `docker compose config`.
3. Rode o teste ponta a ponta para isolar a camada com problema:
   `bash scripts/smoke_test.sh` — ele indica exatamente em qual dos 6 passos falhou.
4. Abra uma issue no
   [repositório](https://github.com/lucasetculbra/django-upload-stack/issues) incluindo a
   saída dos itens acima (sem a sua `SECRET_KEY` e sem senhas).

## Próximos passos

- [Instalação e execução](instalacao.md) — o caminho feliz, do zero ao upload.
- [Docker Compose e Dockerfile](docker-compose.md) — o que cada diretiva faz e por quê.
- [Variáveis de ambiente](variaveis.md) — referência completa do `.env`.
- [Persistência e volumes](persistencia.md) — backup, restore e a prova de persistência.
