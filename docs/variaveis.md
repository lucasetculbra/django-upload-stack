# Variáveis de ambiente

Esta é a página de referência da configuração do projeto. Nenhum valor específico de
ambiente está escrito no código: `config/settings.py` lê tudo de variáveis de ambiente,
de modo que a **mesma imagem** roda na sua máquina, no CI e em um servidor mudando apenas
o arquivo `.env`.

Tudo o que está documentado aqui foi retirado diretamente de `config/settings.py`,
`entrypoint.sh`, `docker-compose.yml`, `nginx/default.conf` e dos workflows em
`.github/workflows/`. Se uma variável não aparece nesta página, ela não é lida por nada
no projeto.

---

## Quem lê o quê

Existem duas camadas diferentes de variáveis, e confundi-las é a causa mais comum de
"mudei o `.env` e nada aconteceu".

| Consumidor | Como recebe os valores | Variáveis envolvidas |
| --- | --- | --- |
| **Docker Compose** (interpolação do arquivo `.env`) | Compose lê o `.env` do diretório do projeto antes de subir qualquer coisa e substitui `${VAR}` dentro do `docker-compose.yml` | `NGINX_PORT`, `WEB_IMAGE`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` |
| **Container `db`** | bloco `environment:` explícito no serviço | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` |
| **Container `web`** | `env_file: .env` + bloco `environment:` + `ENV` da imagem | todas as variáveis de Django |
| **Container `nginx`** | nenhuma variável de ambiente | a configuração é fixa na imagem (`nginx/default.conf`) |
| **`entrypoint.sh`** (dentro do `web`) | ambiente do container | `DJANGO_DB`, `POSTGRES_*`, `DB_WAIT_ATTEMPTS` |
| **`pytest` local** | `config/settings_test.py` define padrões com `os.environ.setdefault` | `DJANGO_DB`, `SECRET_KEY`, `ALLOWED_HOSTS` |

!!! note "Precedência dentro do container `web`"
    A ordem é `environment:` **>** `env_file:` **>** `ENV` da imagem. O `docker-compose.yml`
    fixa `POSTGRES_HOST: db` e `POSTGRES_PORT: 5432` no bloco `environment:`, então esses
    dois valores no seu `.env` **são ignorados pelo container** — eles existem no
    `.env.example` para quem roda o Django fora do Docker. O `Dockerfile` também define
    `DJANGO_DB=postgres`, `MEDIA_ROOT=/app/media` e `STATIC_ROOT=/app/staticfiles` como
    `ENV`, que o `.env` pode sobrescrever.

!!! warning "O `db` nunca recebe a `SECRET_KEY`"
    O serviço `db` usa um bloco `environment:` explícito com apenas três valores, em vez de
    `env_file: .env`. Isso é intencional: o PostgreSQL não tem nenhuma necessidade de
    conhecer a chave da aplicação, e reduzir o alcance de um segredo é mais barato do que
    justificá-lo depois.

---

## Como os valores são interpretados

`config/settings.py` define quatro helpers minúsculos. Entender os quatro evita
praticamente todos os erros de configuração deste projeto.

```python
def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    value = env_str(name, "1" if default else "0").lower()
    return value in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    return int(value) if value else default


def env_csv(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env_str(name, default).split(",") if item.strip()]
```

| Helper | Regra |
| --- | --- |
| `env_str` | Aplica `.strip()` no valor, então espaços acidentais em volta (e o `\r` de um arquivo com fim de linha CRLF) não estragam a leitura. |
| `env_bool` | Verdadeiro **apenas** para `1`, `true`, `yes` ou `on` (sem diferenciar maiúsculas). Qualquer outra coisa — `0`, `false`, `no`, `off`, `sim`, `True!`, vazio — é falso. Não há erro: um valor digitado errado vira `False` silenciosamente. |
| `env_int` | Converte com `int()`. Valor ausente ou vazio usa o padrão; valor não numérico derruba o processo no start com `ValueError: invalid literal for int()`. |
| `env_csv` | Separa por vírgula, aplica `.strip()` em cada item e **descarta entradas vazias**. |

!!! danger "Definir uma variável vazia **não** é o mesmo que omiti-la"
    O padrão de `env_str` só entra em ação quando a variável **não existe** no ambiente.
    Uma linha como `ALLOWED_HOSTS=` no `.env` faz a variável existir com valor `""`, e o
    padrão `localhost,127.0.0.1,web` **não** é aplicado — o resultado é uma lista vazia e
    todo request responde `400 Bad Request`. O mesmo vale para `SECRET_KEY=` (que dispara
    `ImproperlyConfigured`) e `POSTGRES_HOST=`. Para voltar ao padrão, **apague a linha
    inteira**, não apenas o valor.

### Por que `env_csv` descarta entradas vazias

É a diferença entre uma aplicação que sobe e uma que rejeita tudo. `"".split(",")` em
Python devolve `[""]` — uma lista com uma string vazia, e não uma lista vazia:

```python
>>> "".split(",")
['']
>>> "localhost,,127.0.0.1,".split(",")
['localhost', '', '127.0.0.1', '']
```

Se esse `''` chegasse em `ALLOWED_HOSTS`, o Django compararia o host do request com uma
string vazia, nenhuma comparação bateria e cada requisição terminaria em
`DisallowedHost`. Filtrar as entradas vazias também torna inofensiva a vírgula final ou
duplicada que sempre aparece quando alguém edita a lista à mão.

---

## Django core

| Variável | Obrigatória | Padrão | Descrição |
| --- | --- | --- | --- |
| `SECRET_KEY` | **Sim** | — | Chave de assinatura do Django (sessões, tokens CSRF, mensagens). Sem ela o processo aborta com `ImproperlyConfigured`. Veja [SECRET_KEY](#secret_key). |
| `DEBUG` | Não | `0` (falso) | Modo de depuração. Mantenha `0` em qualquer execução com Docker: com `1` o Django serve `/media/` e `/static/` sozinho e você deixa de testar o caminho real, que passa pelo Nginx. |
| `ALLOWED_HOSTS` | Não | `localhost,127.0.0.1,web` | Lista CSV de hosts aceitos. Veja [ALLOWED_HOSTS](#allowed_hosts). |
| `CSRF_TRUSTED_ORIGINS` | Não | *(lista vazia)* | Lista CSV de origens confiáveis para POST, **com esquema e porta**. Veja [CSRF_TRUSTED_ORIGINS](#csrf_trusted_origins). |
| `LOG_LEVEL` | Não | `INFO` | Nível do logger raiz, que escreve em um `StreamHandler` no console — ou seja, aparece em `docker compose logs web`. Valores úteis: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Um nome inválido faz `logging.config.dictConfig` falhar no start. |
| `LANGUAGE_CODE` | Não | `pt-br` | Idioma do Django (afeta o admin e a formatação). Não está no `.env.example` porque o padrão já é o desejado. |
| `TIME_ZONE` | Não | `America/Sao_Paulo` | Fuso usado para exibir datas. `USE_TZ = True` é fixo, então o banco sempre grava em UTC e só a apresentação muda. |
| `DJANGO_SETTINGS_MODULE` | Não | `config.settings` | Definido por `manage.py` e `config/wsgi.py` via `setdefault`, e explicitamente como `ENV` no `Dockerfile`. O `pytest` usa `config.settings_test` (configurado no `pyproject.toml`). |

---

## Banco de dados

| Variável | Obrigatória | Padrão | Descrição |
| --- | --- | --- | --- |
| `DJANGO_DB` | Não | `postgres` | `postgres` ou `sqlite` (comparado em minúsculas). Qualquer outro valor levanta `ImproperlyConfigured`. Veja [DJANGO_DB](#django_db). |
| `POSTGRES_DB` | **Sim** com `DJANGO_DB=postgres` | — | Nome do banco. Também consumido pelo serviço `db` (cria o banco no primeiro boot) e pelo healthcheck `pg_isready`. |
| `POSTGRES_USER` | **Sim** com `DJANGO_DB=postgres` | — | Usuário do banco. Idem acima. |
| `POSTGRES_PASSWORD` | **Sim** com `DJANGO_DB=postgres` | — | Senha do banco. Idem acima. |
| `POSTGRES_HOST` | Não | `db` | Host do PostgreSQL. **Fixado em `db` pelo `environment:` do serviço `web`**; o valor do `.env` só tem efeito ao rodar o Django fora do Docker. |
| `POSTGRES_PORT` | Não | `5432` | Porta do PostgreSQL. Também fixada em `5432` pelo `environment:` do serviço `web`. |
| `POSTGRES_CONN_MAX_AGE` | Não | `60` | `CONN_MAX_AGE` em segundos: quanto tempo cada worker do Gunicorn reaproveita a mesma conexão. `0` fecha a conexão a cada request (mais lento, porém mais previsível). Não está no `.env.example`. |
| `SQLITE_PATH` | Não | `<BASE_DIR>/db.sqlite3` | Caminho do arquivo SQLite. Só é lido quando `DJANGO_DB=sqlite`. Não está no `.env.example`. |
| `DB_WAIT_ATTEMPTS` | Não | `60` | Lido por `entrypoint.sh`, não pelo `settings.py`: número máximo de tentativas de conexão (uma por segundo) antes de o container desistir com exit 1. Veja abaixo. |

!!! note "O que `DB_WAIT_ATTEMPTS` realmente controla"
    O `entrypoint.sh` só executa a espera quando `DJANGO_DB` é `postgres`. Ele tenta abrir
    uma conexão `psycopg` com `connect_timeout=3`, e a cada falha dorme 1 segundo; ao
    atingir `DB_WAIT_ATTEMPTS` ele imprime
    `database still unreachable after N attempts, giving up` e sai com erro — em vez de
    ficar preso para sempre, que é o que transforma um banco fora do ar em um
    `docker compose up --wait` que nunca retorna.

    O `docker-compose.yml` já usa `depends_on: db: condition: service_healthy`, então essa
    espera é um cinto de segurança para os casos que o healthcheck não cobre (banco
    reiniciando, rede lenta, `docker compose up web` isolado). Aumente o valor em máquinas
    lentas, onde o primeiro `initdb` do PostgreSQL demora.

---

## Uploads

| Variável | Obrigatória | Padrão | Descrição |
| --- | --- | --- | --- |
| `MAX_UPLOAD_MB` | Não | `100` | Tamanho máximo de upload aceito pela aplicação, em MB. Deriva `MAX_UPLOAD_BYTES` (`MB * 1024 * 1024`) e `DATA_UPLOAD_MAX_MEMORY_SIZE`. Precisa ficar em sintonia com `client_max_body_size` do Nginx — veja [MAX_UPLOAD_MB](#max_upload_mb). |

Dois limites relacionados são **fixos** em `config/settings.py` e não têm variável de
ambiente:

| Setting | Valor | Efeito |
| --- | --- | --- |
| `FILE_UPLOAD_MAX_MEMORY_SIZE` | `5 * 1024 * 1024` | Arquivos acima de 5 MB são gravados em arquivo temporário em disco em vez de ficarem na memória do worker. |
| `FILE_UPLOAD_PERMISSIONS` / `FILE_UPLOAD_DIRECTORY_PERMISSIONS` | `0o644` / `0o755` | O Nginx roda como o usuário `nginx` e lê o volume de media diretamente; sem permissões explícitas o resultado dependeria do umask herdado pelo Gunicorn, e uploads retornariam `403` ao serem baixados. |

---

## Arquivos estáticos e media

| Variável | Obrigatória | Padrão | Descrição |
| --- | --- | --- | --- |
| `STATIC_ROOT` | Não | `<BASE_DIR>/staticfiles` | Destino do `collectstatic`. A imagem define `STATIC_ROOT=/app/staticfiles` como `ENV`, e o Compose monta o volume nomeado `static_data` nesse caminho. |
| `MEDIA_ROOT` | Não | `<BASE_DIR>/media` | Onde os uploads são gravados. A imagem define `MEDIA_ROOT=/app/media`, e o Compose monta o volume nomeado `media_data` nesse caminho — é isso que faz os arquivos sobreviverem a `docker compose down`. |

Os padrões são relativos ao projeto justamente para que `pytest` e `manage.py runserver`
funcionem em uma máquina Windows sem nenhuma variável definida; dentro do container eles
são substituídos por caminhos absolutos que coincidem com os pontos de montagem dos
volumes.

!!! danger "Mudar `MEDIA_ROOT` no `.env` desconecta os uploads do volume"
    Os volumes são montados em caminhos fixos no `docker-compose.yml`
    (`media_data:/app/media` e `static_data:/app/staticfiles`). Se você apontar
    `MEDIA_ROOT` para outro diretório, o Django passa a gravar na **camada de escrita do
    container**, que é descartada no `docker compose down` — os arquivos somem e a
    garantia central do projeto deixa de valer. Ao alterar essas variáveis, altere também
    os pontos de montagem. Detalhes em [Persistência e volumes](persistencia.md).

---

## Segurança

| Variável | Obrigatória | Padrão | Descrição |
| --- | --- | --- | --- |
| `SECURE_SSL_REDIRECT` | Não | `0` (falso) | Redireciona todo request HTTP para HTTPS (`SecurityMiddleware`). |
| `SECURE_COOKIES` | Não | `0` (falso) | Uma única variável que controla `SESSION_COOKIE_SECURE` **e** `CSRF_COOKIE_SECURE`. |
| `TRUST_PROXY_SSL_HEADER` | Não | `0` (falso) | Quando verdadeiro, define `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`, fazendo o Django confiar no cabeçalho enviado pelo Nginx para saber se a conexão original era HTTPS. |

Nenhuma dessas três está no `.env.example`, porque a stack padrão é HTTP puro. Duas
proteções estão **sempre ligadas** e não dependem de variável: `SECURE_CONTENT_TYPE_NOSNIFF
= True` e `X_FRAME_OPTIONS = "DENY"`. Veja [o grupo SECURE_*](#o-grupo-secure_) para saber o
que ligar atrás de TLS.

---

## Compose e infraestrutura

| Variável | Obrigatória | Padrão | Descrição |
| --- | --- | --- | --- |
| `NGINX_PORT` | Não | `8080` | Porta publicada no host, via `ports: ["${NGINX_PORT:-8080}:80"]`. É a **única** porta publicada da stack; `web` só usa `expose: 8000` e o `db` não expõe nada. |
| `WEB_IMAGE` | Não | `django-upload-stack-web:local` | Nome da imagem da aplicação, via `image: ${WEB_IMAGE:-django-upload-stack-web:local}`. Permite rodar a imagem publicada no GHCR sem alterar o compose. Não está no `.env.example`. |

A sintaxe `${VAR:-padrão}` do Compose usa o padrão quando a variável está **ausente ou
vazia** — diferente dos helpers do `settings.py`, em que vazio é vazio. Já
`${POSTGRES_DB}` (sem `:-`) não tem padrão: se a variável faltar, o Compose emite um aviso
e substitui por string vazia, e o PostgreSQL falha ao inicializar.

Para usar a imagem publicada em vez de construir localmente:

=== "PowerShell"

    ```powershell
    $env:WEB_IMAGE = "ghcr.io/lucasetculbra/django-upload-stack:latest"
    docker compose pull web
    docker compose up -d --no-build --wait
    ```

=== "Git Bash"

    ```bash
    WEB_IMAGE=ghcr.io/lucasetculbra/django-upload-stack:latest \
      docker compose pull web
    WEB_IMAGE=ghcr.io/lucasetculbra/django-upload-stack:latest \
      docker compose up -d --no-build --wait
    ```

!!! tip "Confira a interpolação antes de subir"
    `docker compose config` imprime o compose já com todas as variáveis resolvidas, e
    `docker compose config -q` apenas valida (é exatamente o que o job de smoke test do CI
    faz). É a forma mais rápida de descobrir que o `.env` não está sendo lido.

---

## SECRET_KEY

É a única variável verdadeiramente obrigatória. `config/settings.py` a lê primeiro e, se
ela estiver ausente ou vazia, interrompe a inicialização:

```python
SECRET_KEY = env_str("SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY is required. Copy .env.example to .env and set a unique value "
        "(see docs/instalacao.md)."
    )
```

Falhar imediatamente é deliberado: uma chave padrão embutida no código seria copiada para
produção sem ninguém perceber, e com ela qualquer pessoa consegue forjar sessões e tokens
CSRF assinados.

**Como gerar uma chave:**

=== "PowerShell"

    ```powershell
    python -c "import secrets; print(secrets.token_urlsafe(50))"
    ```

=== "Git Bash"

    ```bash
    python -c "import secrets; print(secrets.token_urlsafe(50))"
    ```

Copie a saída para a linha `SECRET_KEY=` do `.env`. No Git Bash dá para fazer tudo em uma
linha, do mesmo jeito que o CI faz:

```bash
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(50))')|" .env
```

`secrets.token_urlsafe` não é escolha estética: ele produz apenas caracteres
`A-Z a-z 0-9 - _`, ou seja, nunca gera `$`, `#`, aspas ou espaço — exatamente os
caracteres que quebram a interpolação do `.env` pelo Compose (veja o aviso em
[Cuidados com o arquivo .env](#cuidados-com-o-arquivo-env)).

!!! danger "Nunca versione a chave"
    `.gitignore` contém `.env` com a exceção `!.env.example`, e `.dockerignore` exclui
    `.env` e `.env.*` para que nenhum segredo entre na imagem. O valor em `.env.example`
    (`change-me-use-a-unique-random-value-in-your-own-env`) é um marcador: se ele chegar a
    um ambiente real, todos os tokens assinados passam a ser previsíveis.

Trocar a chave em um ambiente que já está no ar invalida todas as sessões ativas, os
cookies CSRF já emitidos e os links de reset de senha. É um efeito aceitável — e é
justamente o que você quer que aconteça se houver suspeita de vazamento.

---

## DJANGO_DB

```python
DJANGO_DB = env_str("DJANGO_DB", "postgres").lower()
```

| Valor | Comportamento |
| --- | --- |
| `postgres` (padrão) | Exige `POSTGRES_DB`, `POSTGRES_USER` e `POSTGRES_PASSWORD`. Se algum faltar, o start aborta listando exatamente quais estão faltando. |
| `sqlite` | Usa `SQLITE_PATH` ou `<BASE_DIR>/db.sqlite3`. Existe para rodar os testes e o `runserver` sem subir banco nenhum. |
| qualquer outra coisa | `ImproperlyConfigured: DJANGO_DB must be 'postgres' or 'sqlite', got '...'`. |

### Por que não existe fallback implícito para SQLite

Seria trivial escrever "tente PostgreSQL e, se não der, use SQLite". Muitos projetos fazem
isso — e é exatamente o que este **não** faz, porque dentro de um container esse fallback
destrói silenciosamente a garantia que o projeto inteiro existe para demonstrar.

Repare no encadeamento:

1. O banco demora alguns segundos a mais para ficar pronto, ou uma senha está errada.
2. Com fallback, o Django escolheria SQLite e o container subiria **saudável**, sem erro
   visível nos logs.
3. O arquivo `db.sqlite3` seria criado em `/app`, que é a camada de escrita do container —
   e **não** é um volume nomeado.
4. Os uploads continuariam indo para o volume `media_data`, mas os registros iriam para o
   SQLite.
5. No `docker compose down` seguinte, o container é removido junto com sua camada de
   escrita. Os arquivos continuariam no volume, porém invisíveis: sem as linhas de
   `UploadedFile`, a listagem em `/` volta vazia.

O resultado seria um bug intermitente, sem mensagem de erro, que só aparece depois de um
ciclo `down` + `up` — o pior tipo de falha possível. Falhar alto e cedo custa um
`docker compose logs web` e resolve o problema na hora. O comentário no próprio
`settings.py` registra essa decisão, e o `Dockerfile` reforça com `ENV DJANGO_DB=postgres`
na imagem.

!!! warning "SQLite é para testes fora do Docker"
    `config/settings_test.py` faz `os.environ.setdefault("DJANGO_DB", "sqlite")` antes de
    importar as settings reais. Como `setdefault` nunca sobrescreve um valor existente, o
    mesmo `pytest` roda contra PostgreSQL bastando exportar `DJANGO_DB=postgres` e os
    `POSTGRES_*` — é assim que o job de testes do CI funciona, e é assim que
    `docker-compose.test.yml` executa a suíte contra o banco real. Veja
    [CI/CD](ci-cd.md).

---

## ALLOWED_HOSTS

```python
ALLOWED_HOSTS = env_csv("ALLOWED_HOSTS", "localhost,127.0.0.1,web")
```

O Django compara o cabeçalho `Host` de cada requisição com essa lista. Se não houver
correspondência, a resposta é **`400 Bad Request`** com `DisallowedHost` no log — e, com
`DEBUG=0`, sem nenhuma explicação na página.

Cada entrada do padrão tem um motivo concreto:

| Entrada | Por que está lá |
| --- | --- |
| `localhost` | É o host que você digita no navegador (`http://localhost:8080`) e, principalmente, o `Host` que o healthcheck do serviço `web` força: ele monta a requisição com `headers={"Host": "localhost"}` para que a sonda funcione independentemente de como a lista estiver configurada. |
| `127.0.0.1` | Mesma coisa, para quem usa o IP em vez do nome. |
| `web` | Cobre chamadas internas na rede do Compose que usem o nome do serviço como host (por exemplo `docker compose exec nginx wget http://web:8000/healthz/`). |

O Nginx repassa `proxy_set_header Host $http_host`, ou seja, **o host que o navegador
enviou chega intacto ao Django, com porta e tudo**. Consequência prática: se você acessa a
stack pelo IP da máquina na rede local, precisa incluir esse IP.

```ini
ALLOWED_HOSTS=localhost,127.0.0.1,web,192.168.0.10
```

### A falha em cascata

Esta é a parte que confunde: um `ALLOWED_HOSTS` errado não produz apenas uma página de
erro, produz uma stack que nunca termina de subir.

1. O healthcheck do `web` faz `GET http://127.0.0.1:8000/healthz/` com `Host: localhost`.
2. Se `localhost` não estiver na lista, o Django responde `400`, o healthcheck exige `200`
   e sai com código 1.
3. Após 5 tentativas o container fica `unhealthy`.
4. O `nginx` declara `depends_on: web: condition: service_healthy`, então **nunca inicia**.
5. `docker compose up -d --wait` fica preso até o timeout, e `curl http://localhost:8080/`
   devolve conexão recusada — sem nenhuma pista de que a causa está no `ALLOWED_HOSTS`.

O diagnóstico está sempre em `docker compose logs web`, procurando por `Invalid HTTP_HOST
header`. Mais sintomas e soluções em [Troubleshooting](troubleshooting.md).

---

## CSRF_TRUSTED_ORIGINS

```python
CSRF_TRUSTED_ORIGINS = env_csv("CSRF_TRUSTED_ORIGINS")
```

Enquanto `ALLOWED_HOSTS` trabalha com **nomes de host**, esta lista trabalha com
**origens completas**, que o Django compara com o cabeçalho `Origin` enviado pelo
navegador em cada POST. As regras são rígidas:

| Regra | Certo | Errado |
| --- | --- | --- |
| Esquema obrigatório | `http://localhost:8080` | `localhost:8080` |
| Porta exata | `http://localhost:8080` | `http://localhost` |
| Sem barra final | `http://localhost:8080` | `http://localhost:8080/` |
| Uma origem por entrada, separadas por vírgula | `http://localhost:8080,http://127.0.0.1:8080` | `http://localhost:8080 http://127.0.0.1:8080` |

`http://localhost:8080` e `http://127.0.0.1:8080` são **origens diferentes** para o
navegador, por isso as duas aparecem no `.env.example`.

!!! warning "Mudou `NGINX_PORT`? Mude também aqui"
    As duas variáveis precisam andar juntas. Se você publicar a stack em `9090`
    (`NGINX_PORT=9090`) e esquecer o `CSRF_TRUSTED_ORIGINS`, a página `/` carrega
    normalmente — é um `GET` — e só o **envio do formulário** falha, com
    `403 Forbidden: CSRF verification failed`. Atualize para:

    ```ini
    NGINX_PORT=9090
    CSRF_TRUSTED_ORIGINS=http://localhost:9090,http://127.0.0.1:9090
    ```

É também por isso que `nginx/default.conf` usa `$http_host` e não `$host` no
`proxy_set_header Host`: `$host` descarta a porta, o Django passaria a enxergar o host como
`localhost` (porta 80 implícita) e a origem calculada — `http://localhost` — deixaria de
bater com o `Origin: http://localhost:8080` que o navegador manda. Todo POST viraria 403, e
o formulário de upload nunca funcionaria. Atrás de TLS, as entradas precisam usar
`https://` e o domínio público.

---

## MAX_UPLOAD_MB

Este limite existe em **dois lugares diferentes**, que precisam ser mantidos em sintonia
manualmente:

| Onde | Valor padrão | Arquivo |
| --- | --- | --- |
| Aplicação | `MAX_UPLOAD_MB=100` | `.env` → `config/settings.py` → `uploads/forms.py` |
| Proxy | `client_max_body_size 100M;` | `nginx/default.conf` (embutido na imagem do Nginx) |

O que acontece com um arquivo grande depende de qual dos dois limites é atingido primeiro:

| Situação | Quem rejeita | O que o usuário vê |
| --- | --- | --- |
| Arquivo dentro dos dois limites | ninguém | `302` e redirecionamento para a listagem |
| Arquivo maior que `client_max_body_size` | **Nginx** | `413 Content Too Large`, uma página de erro do próprio Nginx. O request nunca chega ao Django, nada é registrado no banco e não há mensagem em português |
| Arquivo maior que `MAX_UPLOAD_MB`, porém menor que `client_max_body_size` | **Django** | A página recarrega com `200` e o erro do formulário: `O arquivo excede o limite de 100 MB.` |

A validação da aplicação vive em `uploads/forms.py`:

```python
def clean_file(self):
    uploaded = self.cleaned_data["file"]
    if uploaded.size > settings.MAX_UPLOAD_BYTES:
        raise forms.ValidationError(
            f"O arquivo excede o limite de {settings.MAX_UPLOAD_MB} MB."
        )
    return uploaded
```

!!! tip "Qual dos dois deve ser maior"
    Mantenha `client_max_body_size` **maior ou igual** a `MAX_UPLOAD_MB`. Com o Nginx
    menor, o usuário recebe um `413` cru para arquivos que a aplicação aceitaria — sem
    mensagem útil e sem chance de o formulário explicar o limite. Com o Nginx igual ou
    maior, a mensagem amigável do Django é quem responde. Subir `MAX_UPLOAD_MB` para `500`
    sem tocar no Nginx não aumenta limite algum: todo upload acima de 100 MB continua
    morrendo no proxy.

Como `client_max_body_size` está no `nginx/default.conf`, que é copiado para dentro da
imagem (sem bind mount, que é frágil em hosts Windows/OneDrive), alterá-lo exige
reconstruir o serviço:

```bash
docker compose up -d --build nginx
```

!!! note "`DATA_UPLOAD_MAX_MEMORY_SIZE` não é o limite do arquivo"
    `settings.py` define `DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES`, mas esse setting
    do Django limita o corpo do request **excluindo** os arquivos enviados em `multipart`.
    Quem realmente barra o arquivo grande é o `clean_file` acima; o
    `DATA_UPLOAD_MAX_MEMORY_SIZE` protege contra um corpo de formulário absurdo sem
    arquivo algum.

Para testar o comportamento do `413` sem subir um arquivo de verdade pelo navegador —
lembrando que um POST sem token CSRF responde `403` antes de qualquer verificação de
tamanho, então use um arquivo claramente acima do limite do Nginx, que é rejeitado ainda
mais cedo:

=== "PowerShell"

    ```powershell
    fsutil file createnew big.bin 157286400
    curl.exe -s -o NUL -w "%{http_code}" -F "file=@big.bin" http://localhost:8080/
    ```

=== "Git Bash"

    ```bash
    head -c 150M /dev/zero > big.bin
    curl -s -o /dev/null -w '%{http_code}\n' -F "file=@big.bin" http://localhost:8080/
    ```

---

## O grupo SECURE_*

A stack padrão fala **HTTP puro** na porta 8080, sem certificado. Por isso as três
variáveis de segurança nascem desligadas: ligá-las sem TLS não deixa nada mais seguro, só
quebra a aplicação.

| Variável | Se ligada sem TLS | Consequência |
| --- | --- | --- |
| `SECURE_SSL_REDIRECT=1` | O `SecurityMiddleware` responde `301` para `https://` em **todo** request, inclusive `/healthz/` | O healthcheck do `web` segue o redirecionamento para uma porta que só fala HTTP, falha, o container nunca fica `healthy` e o `nginx` — que depende dele — nunca inicia |
| `SECURE_COOKIES=1` | `SESSION_COOKIE_SECURE` e `CSRF_COOKIE_SECURE` viram `True`, e o navegador passa a enviar esses cookies apenas por HTTPS | O cookie `csrftoken` nunca volta ao servidor, todo POST responde `403` e não é possível fazer login no `/admin/` |
| `TRUST_PROXY_SSL_HEADER=1` | O Django acredita no cabeçalho `X-Forwarded-Proto` para decidir se a conexão é segura | Sem TLS de verdade, o Nginx envia `X-Forwarded-Proto: http` e nada muda — mas combinado com `SECURE_SSL_REDIRECT` esconde um loop de redirecionamento difícil de diagnosticar |

### Por que `TRUST_PROXY_SSL_HEADER` é uma variável, e não um padrão

```python
if env_bool("TRUST_PROXY_SSL_HEADER", False):
    # Only safe because Nginx always overwrites X-Forwarded-Proto with $scheme.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

`SECURE_PROXY_SSL_HEADER` manda o Django confiar em um cabeçalho HTTP — que é, por
definição, algo que o cliente controla. Aqui isso é seguro por dois motivos combinados:
`nginx/default.conf` sempre **sobrescreve** `X-Forwarded-Proto` com `$scheme` (o valor que
um cliente mal-intencionado tivesse enviado é descartado), e o Gunicorn não tem `ports:`
no compose — ele só é alcançável pela rede interna, nunca diretamente do host. Se um dia
alguém publicar a porta 8000 no host, essa premissa deixa de valer, e é por isso que a
confiança é opt-in em vez de padrão.

### Configuração atrás de TLS

Quando a stack ficar atrás de um proxy/CDN com HTTPS de verdade, o `.env` passa a incluir:

```ini
SECURE_SSL_REDIRECT=1
SECURE_COOKIES=1
TRUST_PROXY_SSL_HEADER=1
ALLOWED_HOSTS=uploads.exemplo.com,web
CSRF_TRUSTED_ORIGINS=https://uploads.exemplo.com
```

Repare que `web` permanece em `ALLOWED_HOSTS` (os healthchecks continuam internos) e que
`CSRF_TRUSTED_ORIGINS` passa a usar `https://` sem porta, porque 443 é a porta padrão do
esquema.

---

## Cuidados com o arquivo .env

!!! warning "O Compose interpola o `.env` — certos caracteres corrompem os valores"
    O arquivo `.env` não é lido só pelos containers: o **Docker Compose** o carrega antes de
    tudo para resolver `${NGINX_PORT}`, `${WEB_IMAGE}` e `${POSTGRES_*}` dentro do
    `docker-compose.yml`. Isso impõe restrições ao conteúdo dos valores:

    | Caractere | O que acontece |
    | --- | --- |
    | `$` | É tratado como início de uma interpolação: `pa$$word` vira algo que o Compose tenta expandir, e o valor que chega ao container não é o que você escreveu. Um `$` literal precisaria ser escrito `$$`, como já é feito no healthcheck do `db` |
    | `#` | Pode ser interpretado como início de comentário e truncar o valor |
    | espaços e aspas | Dependendo da versão do Compose, ficam dentro do valor ou são removidos — o comportamento não é o mesmo em todo lugar, então o resultado é imprevisível |

    É exatamente por isso que a chave é gerada com
    `python -c "import secrets; print(secrets.token_urlsafe(50))"`: o alfabeto
    URL-safe (`A-Z a-z 0-9 - _`) não contém nenhum desses caracteres. O CI usa
    `openssl rand -hex 32` pelo mesmo motivo. Se precisar de uma senha com caracteres
    especiais, prefira gerar outra sem eles a tentar escapá-los.

!!! note "Codificação: UTF-8 sem BOM e fim de linha LF"
    Salve o `.env` como **UTF-8 sem BOM**, com fim de linha **LF**. Um BOM (que o
    `Set-Content -Encoding utf8` do Windows PowerShell 5.1 adiciona) vira parte do nome da
    primeira variável: o processo passa a enxergar `﻿SECRET_KEY` em vez de
    `SECRET_KEY`, e o container morre com `ImproperlyConfigured: SECRET_KEY is required` —
    apontando para uma linha que está claramente ali no arquivo.

    Do lado do Django, um `\r` residual é absorvido pelo `.strip()` do `env_str`, mas as
    variáveis usadas pelo Compose (`NGINX_PORT`, `WEB_IMAGE`, `POSTGRES_*`) são consumidas
    cruas. O `.gitattributes` do repositório já declara `.env*  text eol=lf` para manter o
    padrão; a forma mais segura de criar o arquivo é copiar o exemplo byte a byte, em vez de
    redirecionar a saída de um comando:

    === "PowerShell"

        ```powershell
        Copy-Item .env.example .env
        ```

    === "Git Bash"

        ```bash
        cp .env.example .env
        ```

O passo a passo completo de primeira execução está em
[Instalação e execução](instalacao.md).

---

## .env.example completo

Este é o conteúdo exato do arquivo versionado — ele serve de ponto de partida e de
documentação mínima dentro do próprio repositório:

```ini
# Copy to .env and adjust. Values must not contain $, # or spaces: Docker Compose
# interpolates this file, so those characters would silently mangle the value.

# --- Django -----------------------------------------------------------------
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(50))"
SECRET_KEY=change-me-use-a-unique-random-value-in-your-own-env
DEBUG=0
ALLOWED_HOSTS=localhost,127.0.0.1,web
CSRF_TRUSTED_ORIGINS=http://localhost:8080,http://127.0.0.1:8080
LOG_LEVEL=INFO

# --- Database ---------------------------------------------------------------
# "postgres" requires every POSTGRES_* value below; "sqlite" is for local tests only.
DJANGO_DB=postgres
POSTGRES_DB=upload_stack
POSTGRES_USER=upload_stack
POSTGRES_PASSWORD=upload_stack_dev_password
POSTGRES_HOST=db
POSTGRES_PORT=5432

# --- Uploads ----------------------------------------------------------------
# Keep in sync with client_max_body_size in nginx/default.conf.
MAX_UPLOAD_MB=100

# --- Host port for Nginx ----------------------------------------------------
NGINX_PORT=8080
```

As variáveis documentadas nesta página que **não** aparecem acima — `LANGUAGE_CODE`,
`TIME_ZONE`, `SQLITE_PATH`, `POSTGRES_CONN_MAX_AGE`, `STATIC_ROOT`, `MEDIA_ROOT`,
`SECURE_SSL_REDIRECT`, `SECURE_COOKIES`, `TRUST_PROXY_SSL_HEADER`, `DB_WAIT_ATTEMPTS` e
`WEB_IMAGE` — foram deixadas de fora de propósito: seus padrões já são os corretos para a
stack padrão, e um arquivo de exemplo curto é um arquivo que as pessoas leem inteiro. Todas
funcionam normalmente se você as adicionar.

---

## Variáveis definidas pelo CI

Os workflows não usam `.env` versionado (ele nem existe no repositório). Cada job monta o
ambiente de que precisa:

| Workflow | Job | Variáveis de aplicação definidas | Origem do valor |
| --- | --- | --- | --- |
| `ci.yml` | `lint` | nenhuma | — |
| `ci.yml` | `test` | `SECRET_KEY`, `DEBUG=0`, `DJANGO_DB=postgres`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST=localhost`, `POSTGRES_PORT=5432` | bloco `env:` do job, valores literais |
| `ci.yml` | `test` (serviço `postgres`) | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | bloco `env:` do service container, com os mesmos valores do job |
| `ci.yml` | `docs` | nenhuma | — |
| `ci.yml` | `build` | nenhuma | a imagem é construída, não executada |
| `ci.yml` | `smoke` | o `.env` inteiro, criado com `cp .env.example .env` e depois com `SECRET_KEY` e `POSTGRES_PASSWORD` substituídos por valores aleatórios | `openssl rand -hex 32` e `openssl rand -hex 16` via `sed` |
| `pages.yml` | `build` / `deploy` | nenhuma | só constrói e publica o site MkDocs |
| `publish.yml` | `publish` | nenhuma de aplicação (`REGISTRY` e `IMAGE_NAME` são do workflow, não do Django) | `env:` do workflow |

Três detalhes valem atenção:

- **`POSTGRES_HOST=localhost` no job `test`** contrasta com o `db` usado no Compose. No CI
  o PostgreSQL é um *service container* com a porta `5432` mapeada para o runner, então o
  host correto é `localhost`. É justamente por essa divergência que o
  `docker-compose.yml` fixa `POSTGRES_HOST: db` no bloco `environment:` do serviço `web`:
  assim o container sempre fala com o serviço certo, não importa o que o `.env` diga.
- **O job `smoke` não edita `ALLOWED_HOSTS` nem `CSRF_TRUSTED_ORIGINS`**: ele usa os
  valores do `.env.example`, que já casam com `NGINX_PORT=8080` — o mesmo endereço que
  `scripts/smoke_test.sh http://localhost:8080` exercita.
- **A `SECRET_KEY` do job `test` é literal e descartável**
  (`ci-only-secret-key-not-used-in-production`), porque nesse job nada é exposto na rede.
  Já o job `smoke` sobe a stack de verdade e por isso gera uma chave aleatória.

Os detalhes de cada pipeline estão em [CI/CD](ci-cd.md); o papel de cada serviço na stack
está em [Arquitetura](arquitetura.md) e as opções do compose em
[Docker Compose e Dockerfile](docker-compose.md).
