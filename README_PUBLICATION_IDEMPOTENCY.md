# ArmoredCreator — Publicação Idempotente e Recovery Determinístico

**Status:** CONGELADO PARA IMPLEMENTAÇÃO  
**Branch:** `refactor/closure-batch`

## Objetivo

Eliminar a ambiguidade entre `PUBLISHING`, `UNKNOWN`, `RECOVERY` e `PUBLISHED` sem alterar Sync, Vision, Studio ou RVC que já estão funcionando.

A regra central é:

> Um conteúdo possui uma única intenção de publicação e nunca deve ser republicado apenas porque uma resposta externa foi perdida.

## Identidade

Para cada `content_id`:

```
idempotency_key = armoredcreator:content:<content_id>
```

A publicação também registra:

- `destination_chat_id`
- `destination_topic_id`
- `published_message_id`
- `confirmed`
- `verification_status`
- `verified_at`

SQLite continua sendo a verdade interna; Telegram é a realidade externa que precisa ser reconciliada.

## Máquina de publicação

```
PUBLISHING
   ├── CONFIRMED ──> PUBLISHED
   ├── ABSENT ─────> publish_once() ──> CONFIRMED ──> PUBLISHED
   └── UNKNOWN ────> RECOVERY
```

**Regra absoluta:**

```
UNKNOWN != publicar novamente
```

`UNKNOWN` significa que não existe evidência suficiente para decidir. O sistema permanece seguro e tenta reconciliar posteriormente.

## publish_once()

O ArmoredHub agora concentra a decisão em:

```
publish_once(item)
```

Fluxo:

1. criar/persistir a intenção de publicação;
2. consultar o estado atual;
3. se houver confirmação válida, reutilizar o `message_id`;
4. se houver `UNKNOWN`, parar sem enviar;
5. somente se houver evidência de `ABSENT`, enviar;
6. persistir imediatamente o `message_id`;
7. confirmar a publicação;
8. devolver o resultado ao Coordinator.

Não existe publicação paralela.

## Confirmação normal

Quando o Bot API retorna sucesso e um `message_id` real:

```
Telegram
   ↓
message_id
   ↓
publication_message_sent()
   ↓
publication_confirmed()
   ↓
PUBLISHED
```

A resposta bem-sucedida do Bot API já é uma confirmação externa da aceitação da mensagem. Não dependemos de uma segunda consulta MTProto para transformar uma publicação bem-sucedida em sucesso.

A leitura MTProto é usada principalmente para o cenário ambíguo em que o envio pode ter sido aceito, mas a resposta HTTP foi perdida.

## Timeout depois do envio

Cenário:

```
sendVideo()
   ↓
Telegram aceita
   ↓
resposta perdida / timeout
   ↓
UNKNOWN
```

O sistema **não envia novamente**.

Recovery consulta o Telegram.

Primeiro tenta a história exata do tópico, comparando:

- tópico;
- caption;
- mídia;
- filename quando disponível.

Depois utiliza busca textual como fallback.

Se encontrar exatamente uma publicação:

```
message_id
   ↓
CONFIRMED
   ↓
PUBLISHED
```

Se não encontrar e a consulta tiver sido realmente concluída:

```
ABSENT
   ↓
novo publish_once()
```

Se a consulta não puder determinar a realidade:

```
UNKNOWN
   ↓
RECOVERY
```

## Por que a história do tópico é importante

A reconciliação não depende apenas de busca textual.

O Hub consulta diretamente as mensagens do tópico e aplica a identidade determinística localmente.

Isso reduz a dependência de indexação textual do Telegram e permite detectar publicações que existem mesmo quando uma busca por URL não encontra o conteúdo.

## Crash depois da publicação

Se o processo cair depois que Telegram aceitou:

```
PUBLISHING
   ↓
Telegram publicou
   ↓
processo caiu
   ↓
restart
   ↓
Recovery
   ↓
encontra message_id
   ↓
PUBLISHED
```

Resultado esperado:

**uma única publicação.**

## Crash antes da publicação

```
PUBLISHING
   ↓
processo caiu
   ↓
Recovery
   ↓
Telegram = ABSENT
   ↓
publish_once()
```

Resultado esperado:

**uma única publicação.**

## Cleanup

Publicação e limpeza são estados independentes.

Depois da confirmação:

```
PUBLISHED
cleanup_completed = 0
```

é válido.

Se o cleanup falhar, o item continua `PUBLISHED`.

No próximo restart:

```
PUBLISHED + cleanup_completed=0
   ↓
retry cleanup
```

Nunca republicar.

## Coordinator

A arquitetura continua:

```
START_ALL
   ↓
Coordinator
   ↓
Sync
   ↓
Vision
   ↓
Studio
   ↓
Hub
   ↓
Telegram
```

Somente um item ativo.

A contagem de certificação CATCH-UP considera apenas itens que chegaram a:

```
PUBLISHED + cleanup_completed=1
```

Tentativas com falha continuam registradas para diagnóstico, mas não contam como item concluído para o limite de certificação.

## Casos históricos obrigatórios

Os itens reais que motivaram esta etapa:

- 436 — terminou em RECOVERY/UNKNOWN;
- 434 — terminou em RECOVERY/UNKNOWN;
- 432 — confirmou;
- 430 — confirmou após aproximadamente 78 segundos;
- 428 — confirmou.

436 e 434 serão usados como regressão real.

## Proibições

Não resolver esta etapa com:

- sleep arbitrário;
- timeout infinito;
- republicação após UNKNOWN;
- escolha arbitrária entre múltiplas mensagens;
- PUBLISHED sem `message_id`;
- cleanup antes da confirmação;
- segunda fila;
- segundo Publisher concorrente;
- segundo Coordinator;
- nova estrutura de storage.

## Critérios de fechamento

A camada estará fechada quando:

- publicação normal for confirmada;
- crash pós-envio for recuperado sem duplicação;
- crash pré-envio puder publicar;
- UNKNOWN nunca cause republicação cega;
- múltiplas correspondências permaneçam UNKNOWN;
- cleanup falho não transforme PUBLISHED em FAILED;
- restart real reconcilie PUBLISHING/RECOVERY;
- E2E real confirme uma sequência de itens sem duplicação.


## Auditoria de startup

Antes de o Sync abrir a rotina CATCH-UP/LIVE, o Coordinator executa uma varredura determinística com `StartupReconciler`.

A auditoria registra, por identidade `content_id`:

- estado SQLite;
- arquivos físicos em `storage/videos/{content_id}`;
- existência/estado da publicação;
- `published_message_id` quando confirmado;
- cleanup pendente ou concluído;
- workspaces órfãos no storage sem registro SQLite.

Para publicações existentes mas ainda não confirmadas, o Hub pode consultar o Telegram durante esta fase. A auditoria nunca publica e nunca baixa vídeo.

Depois da auditoria, o fluxo normal começa:

```
START
  ↓
STARTUP AUDIT / RECONCILIATION
  ↓
RECOVERY PENDENTE
  ↓
CATCH-UP
  ↓
LIVE
```

O objetivo é iniciar cada execução com uma fotografia clara da realidade, em vez de depender somente do estado deixado pelo processo anterior.

## Laboratório limpo de certificação

O teste final deve começar com banco e artefatos de processamento limpos. O script:

```
scripts/reset_certification_lab.ps1
```

remove o estado de laboratório e pastas legadas conhecidas, preservando código, assets e credenciais/sessão Telegram.

Depois do reset, usar uma coleta curta, por exemplo:

```powershell
$env:ARMORED_SYNC_CATCHUP_LIMIT="3"
$env:ARMORED_REAL_TELEGRAM="1"
$env:ARMORED_HUB_DRY_RUN="0"
.\START_ALL.bat
```

A auditoria e os logs passam a permitir reconstruir claramente, por identidade, o que foi:

```
COLETADO → PROCESSADO → PUBLICADO → CONFIRMADO → LIMPO
```

ou exatamente em qual etapa e com qual erro o item parou.


## Implementação atual

A implementação já contém:

- `ArmoredHub.publish_once()`;
- intenção de publicação persistida antes da reconciliação;
- confirmação imediata quando o Bot API retorna `message_id`;
- reconciliação por histórico do tópico;
- fallback por busca textual;
- proteção contra UNKNOWN → publish;
- preservação de PUBLISHED quando somente cleanup falha;
- contagem separada entre tentativa de processamento e itens concluídos no CATCH-UP.

## Próximo teste

Depois de atualizar o checkout local:

```powershell
git pull origin refactor/closure-batch
git log -1 --oneline
python -m pytest -q
```

Depois dos testes locais, o próximo passo é o teste real controlado com Telegram, começando pela reconciliação dos itens 436/434 e então um E2E curto.

---

**Este documento é a referência congelada para a camada de publicação.**
