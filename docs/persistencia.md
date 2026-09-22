# Persistência e volumes

Esta é a página que prova o critério de aceitação do projeto: **um arquivo enviado pelo
formulário continua existindo, byte a byte, depois de `docker compose down` seguido de
`docker compose up`**. Tudo aqui é consequência de uma única decisão de arquitetura — os
dados mutáveis (uploads, arquivos estáticos coletados e o cluster do PostgreSQL) não moram
dentro dos contêineres, e sim em três volumes nomeados declarados no fim do
`docker-compose.yml`:

```yaml
volumes:
  postgres_data:
  media_data:
  static_data:
```

## Por que um contêiner esquece tudo

Uma imagem Docker é um empilhado de camadas somente leitura. Quando você sobe um
contêiner, o Docker adiciona no topo uma **camada gravável** (*writable layer*), e é nela
que vai parar qualquer coisa que o processo escreva num caminho que não seja um mount.
Essa camada pertence ao contêiner: ela nasce com `docker compose up` e é destruída junto
com o contêiner em `docker compose down`.

Consequência prática, sem volume nenhum:

1. Você sobe a stack e envia `relatorio.pdf`. Os bytes vão para `/app/media/uploads/...`
   na camada gravável do contêiner `dus-web`; a linha correspondente vai para
   `/var/lib/postgresql/data` na camada gravável do `dus-db`.
2. `docker compose down` remove os dois contêineres.
3. `docker compose up -d` cria contêineres **novos** a partir das mesmas imagens. Camada
   gravável nova, vazia. O banco roda `initdb` do zero, a tabela `uploads_uploadedfile`
   não existe mais e o PDF sumiu.

Um **volume nomeado** quebra esse ciclo. Ele é um diretório gerenciado pelo Docker, com
ciclo de vida **independente** do contêiner: sobrevive ao `down`, e quando o contêiner é
recriado o Docker simplesmente monta o mesmo volume de novo no mesmo ponto. O processo
dentro do contêiner não percebe diferença alguma — para o Django, `/app/media` continua
sendo `/app/media`.

!!! note "Por que isto não é um detalhe de infraestrutura"
    O `config/settings.py` se recusa a cair em SQLite por acidente justamente por causa
    disso. `DJANGO_DB` só aceita `postgres` (padrão) ou `sqlite`, e o valor `sqlite`
    precisa ser pedido explicitamente. Um *fallback* silencioso para SQLite gravaria
    `db.sqlite3` na camada gravável do contêiner e o banco inteiro evaporaria no `down` —
    exatamente o que este projeto existe para demonstrar que **não** acontece.

## Volume nomeado, bind mount e volume anônimo

| Tipo | Como se escreve | Onde os dados ficam | Ciclo de vida |
| --- | --- | --- | --- |
| Volume nomeado | `media_data:/app/media` | Área gerenciada pelo Docker (`/var/lib/docker/volumes/...`) | Independente do contêiner; some só com `down -v` ou `docker volume rm` |
| Bind mount | `./media:/app/media` | Uma pasta do host, escolhida por você | Vive com a pasta do host |
| Volume anônimo | `/app/media` (sem nome à esquerda) | Área gerenciada pelo Docker, com nome gerado em hash | Sobrevive ao `down`, mas vira lixo órfão e é removido por `docker volume prune` |

Esta stack usa **volumes nomeados** nos três casos. Os motivos, em ordem de peso:

- **Portabilidade.** Um bind mount amarra o `docker-compose.yml` ao layout de diretórios de
  quem clonou o repositório. O volume nomeado depende apenas do nome do projeto, então o
  mesmo arquivo funciona igual no Windows, no macOS, no Linux e no runner do GitHub
  Actions (veja [CI/CD](ci-cd.md)).
- **Nada de caminho do Windows nem do OneDrive.** O repositório deste projeto vive dentro
  de `OneDrive\Desktop`. Um bind mount dali passaria por tradução de caminho
  (`C:\Users\...` → `/host_mnt/c/Users/...`), pelo cliente de sincronização do OneDrive
  (que pode segurar, mover ou baixar sob demanda um arquivo enquanto o contêiner escreve
  nele) e por uma camada de sistema de arquivos que **não** carrega dono e bits de
  permissão do Linux. O `nginx/Dockerfile` documenta a mesma preocupação: a configuração é
  copiada para dentro da imagem em vez de montada como arquivo único, porque bind mount de
  arquivo único é frágil em host Windows/OneDrive.
- **Permissões previsíveis.** Num volume nomeado o dono e o modo dos arquivos são os reais
  do Linux — e isso é o que permite o Nginx ler os uploads escritos pelo Django (veja
  [Permissões e dono dos arquivos](#permissoes-e-dono-dos-arquivos) mais abaixo). Num bind
  mount vindo do Windows, tudo aparece com dono e modo sintéticos e o teste deixa de provar
  qualquer coisa.
- **Desempenho.** I/O em volume nomeado acontece dentro da VM do Docker Desktop; bind mount
  atravessa a ponte host↔VM a cada `read`/`write`.

O preço é que o conteúdo não é navegável pelo Explorer. A seção
[Inspecionando os volumes por fora](#inspecionando-os-volumes-por-fora) mostra como ver e
extrair o que está lá dentro.

## `down`, `down -v` e `volume prune`

Estes três comandos são a diferença entre "reiniciar a stack" e "perder tudo".

| Comando | Remove contêineres | Remove a rede `backend` | Remove os volumes nomeados |
| --- | --- | --- | --- |
| `docker compose stop` | Não (só para) | Não | Não |
| `docker compose down` | Sim | Sim | **Não** |
| `docker compose down -v` | Sim | Sim | **Sim** |
| `docker volume prune` | — | — | Só volumes anônimos não usados |
| `docker volume prune -a` | — | — | Também volumes nomeados sem contêiner usando |

Detalhando:

- **`docker compose down`** para e remove os contêineres do projeto e a rede criada para
  ele. Os volumes declarados na seção `volumes:` ficam intactos. É exatamente o comando que
  o passo 5/6 do smoke test executa.
- **`docker compose down -v`** (ou `--volumes`) acrescenta a remoção dos volumes nomeados
  declarados no arquivo **e** dos volumes anônimos anexados aos contêineres. É destrutivo e
  irreversível.
- **`docker volume prune`** não olha o `docker-compose.yml`: ele varre o daemon inteiro. A
  partir do Docker Engine 23.0 ele remove, por padrão, apenas volumes **anônimos** que não
  estejam em uso; com `-a`/`--all` ele passa a incluir também os nomeados. Como os volumes
  desta stack são nomeados, um `prune` sem `-a` não os toca — mas com a stack parada e
  `prune -a`, sim.

!!! danger "`down -v` apaga os três volumes de uma vez"
    `docker compose down -v` destrói `postgres_data`, `media_data` e `static_data`. Não há
    lixeira, não há confirmação e não há como desfazer: todos os uploads, todas as linhas
    da tabela e o cluster inteiro do PostgreSQL desaparecem. Use **apenas** quando quiser
    voltar de propósito a um estado de primeira instalação. Para simplesmente reiniciar a
    stack, o comando é `docker compose down` — sem o `-v`.

    O workflow de CI usa `docker compose down -v` no passo final justamente porque o runner
    é descartável e o objetivo ali é não deixar estado entre execuções.

## Os três volumes da stack

O `name: django-upload-stack` no topo do `docker-compose.yml` fixa o nome do projeto, e o
Docker prefixa os volumes com ele. Por isso os nomes reais no daemon são
`django-upload-stack_media_data` e companhia — e continuam os mesmos mesmo que a pasta do
repositório seja renomeada ou clonada em outro lugar. (Sem esse `name:`, o prefixo viria do
nome do diretório e renomear a pasta faria a stack subir apontando para volumes vazios,
o que parece perda de dados e não é.)

| Volume (nome real) | Montado em | Serviço | Modo | O que se perde sem ele |
| --- | --- | --- | --- | --- |
| `django-upload-stack_postgres_data` | `/var/lib/postgresql/data` | `db` | leitura/escrita | O cluster inteiro: tabelas, migrações aplicadas, e as linhas de `uploads_uploadedfile`. A listagem da página inicial volta vazia mesmo com os arquivos ainda no disco. |
| `django-upload-stack_media_data` | `/app/media` | `web` | leitura/escrita | Os bytes dos uploads. O banco continuaria listando os nomes, mas cada link `/media/...` responderia **404** — registros órfãos apontando para arquivos que não existem mais. |
| | `/vol/media` | `nginx` | **somente leitura** (`:ro`) | |
| `django-upload-stack_static_data` | `/app/staticfiles` | `web` | leitura/escrita | O CSS/JS coletado pelo `collectstatic`. A aplicação continua funcionando, mas sem estilo até o próximo start. |
| | `/vol/static` | `nginx` | **somente leitura** (`:ro`) | |

Três observações que explicam o *porquê* desse desenho:

- **`media_data` e `static_data` são montados em dois serviços ao mesmo tempo.** É o mesmo
  volume, visto de dois pontos: o `web` escreve em `/app/media`, o `nginx` lê em
  `/vol/media` e serve direto com `alias /vol/media/;`. Nenhum byte de upload passa pelo
  Gunicorn na hora do download — o Nginx entrega o arquivo do volume.
- **O `:ro` do lado do Nginx é defesa em profundidade.** O Nginx é o único serviço com
  porta publicada no host (`8080`), ou seja, a maior superfície de ataque da stack. Montado
  como somente leitura, ele não consegue alterar nem apagar um upload nem que quisesse.
- **`static_data` é reconstruído a cada start.** O `entrypoint.sh` roda
  `python manage.py collectstatic --noinput --clear` em todo boot, o que esvazia e repovoa
  o `STATIC_ROOT`. Esse volume existe por desempenho e por coerência de deploy, não por
  persistência de dados. **É por isso que arquivo de usuário nunca pode ser gravado em
  `STATIC_ROOT`**: o próximo `up` apagaria tudo. Upload é `MEDIA_ROOT`; `STATIC_ROOT` é
  artefato derivado do código.

!!! warning "Mudar `POSTGRES_PASSWORD` depois do primeiro `up` não tem efeito"
    A imagem `postgres:17-alpine` só executa o `initdb` — e só aplica `POSTGRES_DB`,
    `POSTGRES_USER` e `POSTGRES_PASSWORD` — quando `/var/lib/postgresql/data` está
    **vazio**. Como esse diretório é o volume `postgres_data`, ele deixa de estar vazio
    depois do primeiro start. Se você trocar a senha no `.env` e der `up` de novo, o
    PostgreSQL continua com a senha antiga, o Django passa a mandar a nova, e o
    `entrypoint.sh` fica preso no laço de espera até estourar `DB_WAIT_ATTEMPTS` (60
    tentativas) com `password authentication failed`.

    Para realmente trocar a senha, escolha um dos dois caminhos:

    ```bash
    # (a) alterar a senha no banco existente, preservando os dados
    docker compose exec db psql -U upload_stack -d upload_stack \
        -c "ALTER USER upload_stack WITH PASSWORD 'nova_senha';"
    # depois atualize POSTGRES_PASSWORD no .env e rode: docker compose up -d

    # (b) recomeçar do zero, APAGANDO o banco (mas não os uploads)
    docker compose down
    docker volume rm django-upload-stack_postgres_data
    docker compose up -d --wait
    ```

    O caminho (b) deixa os arquivos em `media_data` sem linha correspondente no banco —
    eles continuam no volume e acessíveis pela URL direta, mas somem da listagem. Mais
    sintomas e diagnósticos em [Troubleshooting](troubleshooting.md).

## Como o Django é ligado ao volume

A ponte entre o modelo e o volume são duas configurações do `config/settings.py`:

```python
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env_str("MEDIA_ROOT") or (BASE_DIR / "media"))
```

- `MEDIA_ROOT` é o diretório **no disco** onde os bytes são gravados. No contêiner ele vem
  de uma variável definida no `Dockerfile` (`MEDIA_ROOT=/app/media`), que é justamente o
  ponto de montagem do volume `media_data`. Fora do contêiner (pytest, `runserver`) o
  padrão relativo `BASE_DIR / "media"` mantém tudo funcionando no Windows.
- `MEDIA_URL` é o **prefixo de URL** público. Ele não toca em disco: só define que
  `item.file.url` no template renderize `/media/uploads/2026/09/arquivo.bin`. Quem atende
  esse prefixo em produção é o Nginx, pelo `location /media/`.

O modelo `uploads/models.py` decide o caminho dentro do `MEDIA_ROOT`:

```python
class UploadedFile(models.Model):
    file = models.FileField(upload_to="uploads/%Y/%m/", verbose_name="arquivo")
    original_name = models.CharField(max_length=255, verbose_name="nome original")
    size = models.PositiveBigIntegerField(default=0, verbose_name="tamanho (bytes)")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="enviado em")
```

O `upload_to="uploads/%Y/%m/"` é expandido com a data do envio, então um arquivo enviado em
setembro de 2026 aterrissa em `/app/media/uploads/2026/09/` e é publicado em
`/media/uploads/2026/09/`. O particionamento por ano/mês evita um diretório único com
dezenas de milhares de entradas, que degrada listagem e `stat` no sistema de arquivos.

### Os dados vivem em dois lugares

Este é o ponto conceitual mais importante da página. **Um upload não é uma coisa só — são
duas, em dois volumes diferentes:**

| Parte | Onde fica | Volume | Como se verifica |
| --- | --- | --- | --- |
| Os bytes do arquivo | `MEDIA_ROOT/uploads/AAAA/MM/nome` | `media_data` | Baixar `/media/...` e comparar o conteúdo |
| A linha de metadados (`file`, `original_name`, `size`, `uploaded_at`) | Tabela `uploads_uploadedfile` no PostgreSQL | `postgres_data` | A listagem da página inicial |

O campo `file` no banco guarda apenas o **caminho relativo** (`uploads/2026/09/nome.bin`),
nunca o conteúdo. É a combinação dos dois que faz a aplicação funcionar, e é por isso que a
demonstração precisa checar **as duas coisas**:

- Se só o `postgres_data` sobrevivesse: a tabela listaria os arquivos e todo link daria 404.
- Se só o `media_data` sobrevivesse: os arquivos estariam no volume, servidos normalmente
  por URL direta, mas a página inicial mostraria "Nenhum arquivo enviado ainda".

Por isso o passo 6/6 do smoke test faz duas asserções distintas: **o registro ainda está na
listagem** (banco) e **o download continua byte a byte idêntico** (mídia).

!!! note "Apagar a linha não apaga o arquivo"
    O Django não remove o arquivo do disco quando o registro é excluído (comportamento
    padrão desde a versão 1.3, para não destruir dados numa transação revertida). Excluir um
    `UploadedFile` pelo admin deixa os bytes órfãos em `media_data`. É um detalhe
    esperado — e mais um lembrete de que os dois volumes têm ciclos de vida próprios.

## Demonstração automatizada: o smoke test

O `scripts/smoke_test.sh` executa a prova inteira de ponta a ponta, incluindo o
`down`/`up`. Rode-o **a partir da raiz do repositório** e com a stack já no ar (veja
[Instalação e execução](instalacao.md)), porque o passo 5 chama `docker compose` no
diretório de trabalho atual.

=== "Git Bash"

    ```bash
    docker compose up -d --wait
    bash scripts/smoke_test.sh
    ```

=== "PowerShell"

    ```powershell
    docker compose up -d --wait
    & "C:\Program Files\Git\bin\bash.exe" scripts/smoke_test.sh
    ```

O script é `bash` (usa `mktemp`, `cmp`, `head -c /dev/urandom`), então no Windows ele
precisa do Git Bash ou do WSL; o PowerShell sozinho não o executa. A URL base é opcional e
o padrão é `http://localhost:8080` — passe outra se tiver mudado `NGINX_PORT` (veja
[Variáveis de ambiente](variaveis.md)):

```bash
bash scripts/smoke_test.sh http://localhost:8080
```

### Saída real de uma execução

Execução capturada em 22/09/2026 nesta máquina (as cores ANSI foram removidas):

```text
OK   generated test file smoke-20260922-121618-2333.bin (65536 bytes)

==> 1/6 application answers through Nginx
    OK   GET / -> 200
    OK   GET /healthz/ reports database ok

==> 2/6 upload is rejected without a CSRF token
    OK   POST without token -> 403

==> 3/6 upload with a CSRF token
    OK   POST with token -> 302

==> 4/6 file is listed and served by Nginx from the media volume
    OK   listing links to /media/uploads/2026/09/smoke-20260922-121618-2333.bin
    OK   downloaded file is byte-for-byte identical

==> 5/6 recreating the stack (docker compose down, then up)
    OK   containers recreated

==> 6/6 data survived the recreation
    OK   record still listed (postgres_data volume persisted)
    OK   file still served with identical bytes (media_data volume persisted)

SMOKE TEST PASSED - uploads survive down/up
```

### Lendo a saída linha a linha

| Linha | O que ela prova |
| --- | --- |
| `generated test file smoke-20260922-121618-2333.bin (65536 bytes)` | O arquivo de teste são 64 KiB de `/dev/urandom`, com nome único (data + PID). Conteúdo aleatório garante que uma comparação byte a byte só passa se forem literalmente os mesmos bytes; nome único impede que um resto de execução anterior seja confundido com o arquivo novo. |
| `GET / -> 200` | A cadeia completa responde: host → Nginx (`:8080`) → `proxy_pass` → Gunicorn (`web:8000`) → Django. |
| `GET /healthz/ reports database ok` | A view `healthz` executou `SELECT 1` de verdade no PostgreSQL. Um 200 na home poderia vir de cache ou de página sem banco; este não. |
| `POST without token -> 403` | O CSRF está ativo — o 302 do passo seguinte é um upload aceito de verdade, não uma proteção desligada. |
| `POST with token -> 302` | O upload foi aceito e salvo. O 302 é o *redirect* do padrão Post/Redirect/Get de `uploads/views.py`. |
| `listing links to /media/uploads/2026/09/smoke-...bin` | O `upload_to="uploads/%Y/%m/"` foi aplicado, a linha existe no banco e o template renderizou `item.file.url` com o prefixo `MEDIA_URL`. |
| `downloaded file is byte-for-byte identical` | O `cmp -s` passou: o Nginx serviu o arquivo direto do volume `media_data`, sem corrupção e sem passar pelo Django. |
| `containers recreated` | Aqui está o coração: `docker compose down` (que remove `dus-db`, `dus-web` e `dus-nginx`, com suas camadas graváveis) seguido de `docker compose up -d --wait --wait-timeout 300`. Os contêineres novos não têm relação com os antigos — só os volumes são os mesmos. |
| `record still listed (postgres_data volume persisted)` | O `grep` do nome do arquivo na listagem achou a linha: o cluster do PostgreSQL sobreviveu. |
| `file still served with identical bytes (media_data volume persisted)` | Novo download, novo `cmp -s`, mesmos 65536 bytes: o volume de mídia sobreviveu. |

!!! tip "O mesmo script roda no CI"
    O job `smoke` do `.github/workflows/ci.yml` executa
    `bash scripts/smoke_test.sh http://localhost:8080` num runner limpo, depois de
    `docker compose up -d --build --wait`. Ou seja, o critério de aceitação é verificado
    automaticamente a cada push na `main` e a cada pull request, não só na máquina de quem
    desenvolve. Detalhes em [CI/CD](ci-cd.md).

## Demonstração manual, passo a passo

Se você quiser ver com os próprios olhos, sem script. Todos os comandos rodam na raiz do
repositório.

**1. Suba a stack e envie um arquivo pelo navegador.**

```bash
docker compose up -d --wait
```

Abra <http://localhost:8080>, escolha qualquer arquivo e clique em **Enviar**. Ele aparece
na tabela "Arquivos enviados" com tamanho e data.

**2. Copie o link do arquivo.** Clique com o botão direito no nome dele na tabela e copie o
endereço. Ele tem a forma `/media/uploads/2026/09/<nome>`. Guarde a URL completa, por
exemplo `http://localhost:8080/media/uploads/2026/09/relatorio.pdf`.

!!! note "O clique baixa em vez de abrir"
    O `location /media/` do `nginx/default.conf` envia
    `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff` e uma CSP
    `default-src 'none'; sandbox`. Conteúdo enviado por usuário é não confiável e mora na
    mesma origem da aplicação; forçar download em vez de renderização evita que um HTML ou
    SVG malicioso execute script no contexto do site.

**3. Baixe uma cópia de referência, antes de derrubar nada.**

=== "PowerShell"

    ```powershell
    curl.exe -fsS -o antes.bin "http://localhost:8080/media/uploads/2026/09/relatorio.pdf"
    ```

=== "Git Bash"

    ```bash
    curl -fsS -o antes.bin "http://localhost:8080/media/uploads/2026/09/relatorio.pdf"
    ```

**4. Derrube a stack — sem `-v`.**

```bash
docker compose down
```

**5. Confirme que não sobrou contêiner nenhum.**

```bash
docker compose ps
```

A saída traz só o cabeçalho, sem nenhuma linha: os três contêineres foram removidos, e com
eles as respectivas camadas graváveis.

**6. Confirme que os volumes continuam lá.**

```bash
docker volume ls
```

```text
DRIVER    VOLUME NAME
local     django-upload-stack_media_data
local     django-upload-stack_postgres_data
local     django-upload-stack_static_data
```

Esta é a imagem exata do conceito: **zero contêineres, três volumes.** Os dados existem
neste momento sem que nada esteja rodando.

**7. Suba de novo.**

```bash
docker compose up -d --wait
```

O `--wait` só retorna quando os três healthchecks passam — inclusive o do Nginx, que atinge
`/healthz/` através do proxy, ou seja, "saudável" significa que a cadeia inteira responde.

**8. Recarregue <http://localhost:8080>.** O arquivo continua na tabela, com o mesmo
tamanho e a mesma data de envio (`uploaded_at` veio do banco, não foi regravado).

**9. Baixe de novo e compare os bytes.**

=== "PowerShell"

    ```powershell
    curl.exe -fsS -o depois.bin "http://localhost:8080/media/uploads/2026/09/relatorio.pdf"
    Get-FileHash antes.bin, depois.bin | Format-List Path, Hash
    ```

=== "Git Bash"

    ```bash
    curl -fsS -o depois.bin "http://localhost:8080/media/uploads/2026/09/relatorio.pdf"
    cmp antes.bin depois.bin && echo "idêntico"
    sha256sum antes.bin depois.bin
    ```

Hashes iguais (ou `cmp` silencioso) = critério de aceitação cumprido.

## Inspecionando os volumes por fora

**Listar os volumes do projeto:**

```bash
docker volume ls --filter label=com.docker.compose.project=django-upload-stack
```

**Ver os metadados de um volume:**

```bash
docker volume inspect django-upload-stack_media_data
```

Trecho da saída (o JSON completo tem mais campos):

```json
[
    {
        "Driver": "local",
        "Labels": {
            "com.docker.compose.project": "django-upload-stack",
            "com.docker.compose.volume": "media_data"
        },
        "Mountpoint": "/var/lib/docker/volumes/django-upload-stack_media_data/_data",
        "Name": "django-upload-stack_media_data",
        "Scope": "local"
    }
]
```

Para extrair só o caminho:

```bash
docker volume inspect -f '{{ .Mountpoint }}' django-upload-stack_media_data
```

!!! warning "Esse `Mountpoint` não existe no seu Windows"
    `/var/lib/docker/volumes/...` é um caminho **dentro da VM do Docker Desktop** (no
    Windows, uma distribuição WSL 2 chamada `docker-desktop`). Não adianta procurar por ele
    no Explorer nem no `C:\`. O jeito certo de olhar o conteúdo é montar o volume num
    contêiner descartável — que é o que o comando a seguir faz.

**Listar o conteúdo sem subir a stack:**

```bash
docker run --rm -v django-upload-stack_media_data:/v alpine ls -laR /v
```

Um contêiner `alpine` efêmero monta o volume em `/v`, lista tudo recursivamente e se
autodestrói (`--rm`). Você deve ver a hierarquia criada pelo `upload_to`:

```text
/v:
...
drwxr-xr-x    3 1000     1000          4096 Sep 22 15:16 uploads

/v/uploads:
...
drwxr-xr-x    3 1000     1000          4096 Sep 22 15:16 2026

/v/uploads/2026/09:
...
-rw-r--r--    1 1000     1000         65536 Sep 22 15:16 smoke-20260922-121618-2333.bin
```

Note que o dono aparece como **`1000 1000`** e não como `app app`: a imagem `alpine` não
tem um usuário com uid 1000 no `/etc/passwd`, então o `ls` mostra o número cru. É o mesmo
dono — só falta quem traduza. Dentro do contêiner `web`, que tem o usuário `app`, o mesmo
arquivo aparece como `app app`.

O mesmo truque serve para os outros dois volumes:

```bash
docker run --rm -v django-upload-stack_static_data:/v alpine ls /v
docker run --rm -v django-upload-stack_postgres_data:/v alpine ls /v
```

!!! danger "Nunca edite `postgres_data` por fora"
    Montar o volume do PostgreSQL num `alpine` para olhar é inofensivo. Escrever,
    renomear ou apagar qualquer coisa ali com o banco parado — ou pior, com ele rodando —
    corrompe o cluster de forma que nenhum `up` conserta. Para mexer em dados, use
    `docker compose exec db psql` ou o `pg_dump` da seção de backup.

## Permissões e dono dos arquivos { #permissoes-e-dono-dos-arquivos }

Um volume nomeado só funciona neste desenho porque as permissões batem entre três
processos diferentes: o Gunicorn (que escreve), o worker do Nginx (que lê) e o `initdb` do
PostgreSQL. A sequência que faz isso dar certo tem três peças.

**1. A imagem cria os diretórios com o dono certo.** O `Dockerfile` cria o usuário `app`
com uid/gid fixos e prepara os pontos de montagem **antes** de trocar para `USER app`:

```dockerfile
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/sh app
...
RUN mkdir -p /app/media /app/staticfiles && chown -R app:app /app
```

Isso importa por causa de uma regra do Docker que passa despercebida: **quando um volume
nomeado vazio é montado sobre um diretório que existe na imagem, o Docker copia o conteúdo
e também o dono e o modo daquele diretório para o volume.** Se `/app/media` não existisse na
imagem, o volume nasceria pertencendo a `root:root` e o Gunicorn, rodando como `app`, levaria
`Permission denied` no primeiro upload.

**2. O `depends_on` garante quem monta primeiro.** O `nginx` declara:

```yaml
depends_on:
  web:
    condition: service_healthy
```

O comentário no `docker-compose.yml` explica o motivo real, que vai além de ordenar o
start: isso **garante que o `web` monte os volumes de mídia e estáticos primeiro**. Se o
Nginx chegasse antes num volume vazio, quem semearia o diretório seria a imagem do Nginx —
com o dono dela, não o `app`.

**3. O Django força modos explícitos.** Em `config/settings.py`:

```python
FILE_UPLOAD_PERMISSIONS = 0o644
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755
```

Sem isso, o modo do arquivo dependeria do `umask` herdado pelo processo do Gunicorn — um
detalhe que pode mudar entre versões de imagem base e render arquivos `0600`, ilegíveis
para o usuário `nginx`.

**Verificando o resultado real.** Com a stack no ar:

```bash
docker compose exec web stat -c '%A %U:%G %n' /app/media
docker compose exec web sh -c "find /app/media -type f -exec stat -c '%A %U:%G %n' {} +"
```

O que se observa:

```text
drwxr-xr-x app:app /app/media
-rw-r--r-- app:app /app/media/uploads/2026/09/smoke-20260922-121618-2333.bin
```

Diretório `drwxr-xr-x`, arquivos `-rw-r--r--`, tudo de `app:app`. O bit de leitura para
"outros" é exatamente o que permite ao worker do Nginx — que roda como o usuário `nginx`,
um uid diferente, em **outro contêiner** — abrir o arquivo em `/vol/media` e entregá-lo ao
navegador. Para confirmar do lado do Nginx:

```bash
docker compose exec nginx ls -l /vol/media/uploads/2026/09
```

!!! tip "403 no `/media/` quase sempre é permissão"
    Se a listagem mostra o arquivo (banco ok) mas o download devolve 403, o suspeito número
    um é dono/modo no volume — em geral um volume que foi semeado por `root` porque a ordem
    de montagem mudou. A correção limpa é `docker compose down`,
    `docker volume rm django-upload-stack_media_data` (isso apaga os uploads) e `up` de
    novo. Mais casos em [Troubleshooting](troubleshooting.md).

## Backup e restauração

Persistência não é backup: o volume sobrevive ao `down`, mas não a um `down -v`, a um
`docker volume rm` nem ao fim da vida útil do disco. As duas cópias abaixo cobrem as duas
metades de um upload.

### Arquivos enviados (`media_data`)

Um contêiner efêmero monta o volume em `/v`, monta o diretório atual em `/backup` e
empacota um do outro:

=== "Git Bash"

    ```bash
    MSYS_NO_PATHCONV=1 docker run --rm \
        -v django-upload-stack_media_data:/v \
        -v "$PWD:/backup" \
        alpine tar czf /backup/media.tar.gz -C /v .
    ```

=== "PowerShell"

    ```powershell
    docker run --rm `
        -v django-upload-stack_media_data:/v `
        -v "${PWD}:/backup" `
        alpine tar czf /backup/media.tar.gz -C /v .
    ```

O `-C /v .` empacota o **conteúdo** do volume, sem o prefixo `v/` — assim a restauração cai
direto na raiz do volume de destino. No Git Bash, o `MSYS_NO_PATHCONV=1` impede que o MSYS
converta `/backup` num caminho do Windows antes de o Docker recebê-lo.

Restaurar para um volume vazio (crie-o subindo a stack uma vez, ou com `docker volume create`):

```bash
docker run --rm -v django-upload-stack_media_data:/v -v "$PWD:/backup" \
    alpine sh -c "tar xzf /backup/media.tar.gz -C /v"
```

!!! warning "Restaure com a stack parada"
    Rode o `tar xzf` com `docker compose down` já executado. Restaurar por baixo de um
    Gunicorn em execução pode deixar o volume num estado misto entre a cópia antiga e a
    nova. E depois de restaurar arquivos, confira o dono: se o `tar` foi criado em outra
    máquina, pode ser necessário
    `docker run --rm -v django-upload-stack_media_data:/v alpine chown -R 1000:1000 /v`.

### Banco de dados (`postgres_data`)

Para o banco, o correto é um *dump* lógico com o cliente do próprio PostgreSQL, e não copiar
arquivos do volume — que só seria consistente com o servidor parado:

```bash
docker compose exec db pg_dump -U upload_stack upload_stack > dump.sql
```

Troque `upload_stack` pelos valores de `POSTGRES_USER` e `POSTGRES_DB` do seu `.env` (os
padrões estão em `.env.example`; veja [Variáveis de ambiente](variaveis.md)). Para restaurar
num banco já criado:

```bash
docker compose exec -T db psql -U upload_stack -d upload_stack < dump.sql
```

O `-T` desliga a alocação de TTY, sem a qual o redirecionamento de `stdin` não chega ao
`psql`.

!!! tip "Backup completo = os dois juntos"
    Um `dump.sql` sem o `media.tar.gz` restaura uma listagem cheia de links quebrados; um
    `media.tar.gz` sem o `dump.sql` restaura arquivos que a aplicação não conhece. Guarde
    sempre o par, feito no mesmo momento e com a stack parada.

---

**Para continuar:** o desenho dos serviços e o caminho de uma requisição estão em
[Arquitetura](arquitetura.md); a leitura linha a linha dos arquivos de build está em
[Docker Compose e Dockerfile](docker-compose.md); e os sintomas mais comuns de volume estão
catalogados em [Troubleshooting](troubleshooting.md).
