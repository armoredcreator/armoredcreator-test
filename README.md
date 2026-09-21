# ArmoredCreator Test

Laboratório isolado da reconstrução do ArmoredCreator.

> **Regra:** este repositório é o laboratório. O repositório oficial e a branch `audit/baseline-2026-09-19` permanecem intocáveis.

## Objetivo

Reconstruir o comportamento funcional confirmado no backup com arquitetura canônica, portátil, idempotente, sequencial e recuperável.

Regra central:

**SQLite = verdade. Filesystem = projeção/runtime. Telegram = realidade externa.**

Fluxo:

```
Telegram fonte
    ↓
ArmoredSync
    ├── CATCH-UP histórico
    └── LIVE monitoramento
    ↓
SQLite + storage/videos/{telegram_message_id}/
    ↓
Coordinator
    ↓
Pipeline sequencial — 1 item ativo
    ↓
Vision
    ↓
Studio
    ↓
Hub
    ↓
Telegram destino + confirmação
    ↓
PUBLISHED
    ↓
cleanup
    ↓
próximo item
```

## 1. ArmoredSync: CATCH-UP → LIVE

O Sync somente descobre e ingere conteúdo da fonte. Ele não possui fila física própria e não executa Vision/Studio/Hub.

O mesmo Sync possui dois modos:

```
CATCH_UP
  ↓
histórico Telegram
  ↓
todos os tópicos
  ↓
descoberta de candidatos
  ↓
ingestão canônica
  ↓
fim do histórico
  ↓
LIVE
  ↓
novas mensagens
  ↓
mesma regra de candidato
  ↓
mesma ingestão
```

Histórico e LIVE usam a mesma identidade: **telegram_message_id**.

### Regra de candidato

Comportamento preservado do backup:

1. vídeo + link Shopee na própria mensagem; ou
2. vídeo + link Shopee na mensagem imediatamente seguinte, desde que essa mensagem seguinte não seja vídeo.

Não procurar links arbitrariamente distantes.

### CATCH-UP

A coleta histórica percorre todos os tópicos da fonte.

Cada candidato válido:

```
Telegram ID 1383
    ↓
SQLite
    ↓
storage/videos/1383/
    ↓
1383_{tail_da_url_original}.mp4
```

O download vai diretamente para o workspace canônico.

Não criar cópias em:

```
storage/sync/
storage/queue/
storage/pipeline/
storage/input/
storage/output/
```

O fim do histórico é persistido no SQLite como:

```
CATCH_UP → LIVE
```

Não existe JSON separado para esse estado.

### LIVE

Depois do CATCH-UP, o mesmo Sync monitora a fonte.

O SQLite mantém checkpoints por tópico:

```
sync_topics
    topic_id
    topic_name
    last_seen_message_id
```

O monitoramento consulta mensagens novas a partir desses checkpoints. Uma pequena sobreposição no limite permite detectar corretamente o caso:

```
vídeo sem link
    ↓
mensagem seguinte com Shopee
```

A sobreposição é deduplicada pelo ID Telegram.

Os checkpoints são persistentes para que uma parada/reinício não dependa da memória do processo.

## 2. Coleta não significa processamento paralelo

Mesmo que o Sync descubra:

```
1383
1384
1385
1386
1387
```

o Pipeline permanece:

```
1383 → Vision → Studio → Hub → cleanup
                                      ↓
1384 → Vision → Studio → Hub → cleanup
                                      ↓
1385 → ...
```

**Somente um item fica ativo no processamento.**

O Sync pode descobrir vários itens, mas isso não cria vários Studios nem processamento concorrente.

## 3. SQLite como fila lógica

A fila não é uma pasta.

Exemplo:

```
content_id | state
-----------|--------
1383       | STUDIO
1384       | RECEIVED
1385       | RECEIVED
```

O banco mantém identidade, estado, caminhos, URLs, tentativas, recovery, publicação e erros.

Não criar filas físicas de vídeo.

## 4. Coordinator

O Coordinator é a composição central:

1. recupera itens pendentes no startup;
2. executa CATCH-UP se ainda não terminou;
3. processa um item por vez;
4. depois mantém o LIVE;
5. entrega itens novos ao mesmo Pipeline;
6. não cria pipeline separado para LIVE.

A ordem operacional é sempre:

```
Sync → DB → Coordinator → Vision → Studio → Hub → confirmação → cleanup
```

## 5. Storage canônico

Cada conteúdo possui exatamente um workspace:

```
storage/
├── database/
│   └── armoredcreator.db
├── videos/
│   └── {telegram_message_id}/
│       ├── {telegram_message_id}_{tail_original}.mp4
│       ├── {telegram_message_id}_.mp4
│       └── {telegram_message_id}_{tail_afiliado}.mp4
├── logs/
└── backups/
```

Exemplo real:

```
storage/videos/1383/1383_8KolJcZrfU.mp4
```

**Importante:** `finallinkoriginal` era apenas um exemplo antigo, não um nome literal obrigatório.

Regras:

- original é imutável e permanente;
- original nunca é sobrescrito;
- working/result são artefatos de runtime;
- temporários ficam dentro do workspace do item;
- cleanup só ocorre depois de PUBLISHED;
- cleanup preserva o original;
- nomes físicos são projeção, não fonte de verdade.

## 6. ArmoredVision

Vision recebe um item já ingerido.

Responsabilidades:

- resolver a URL Shopee;
- obter produto;
- obter/generar affiliate URL;
- registrar affiliate_name e affiliate_url no SQLite.

Vision não cria fila física.

## 7. ArmoredStudio

Existe **um único ArmoredStudio**. V1 e V2 não são modos alternativos.

### Analysis

```
ArmoredStudio/analysis/
├── video.py
├── blackbar.py
├── banner_analyzer.py
├── banner.py
├── veo_detector.py
├── gemini_detector.py
├── export_planner.py
├── export_plan_validator.py
├── export_executor.py
└── logger.py
```

### Processing

```
ArmoredStudio/processing/
├── rvc.py
├── tool_paths.py
└── finalizer.py
```

### RVC

RVC é **funcionalidade interna do ArmoredStudio**, não um projeto externo.

Código:

```
ArmoredStudio/processing/rvc.py
```

Runtime local:

```
ArmoredStudio/runtime/rvc/
├── env/
├── models/
│   ├── becca/
│   ├── elsa/
│   ├── jessie/
│   ├── leticia/
│   ├── marilia/
│   ├── melody/
│   └── sarah/
└── output/
```

O runtime é ignorado pelo Git por ser pesado/local. Isso **não** torna o RVC externo ao Studio.

O modelo .pth é obrigatório para a voz selecionada. O .index é opcional quando o runtime funciona sem ele.

Ausência de RVC/assets no processamento real é erro explícito. Não existe fallback silencioso para cópia.

Cópia é somente para testes determinísticos:

```
ARMORED_STUDIO_ALLOW_COPY=1
ARMORED_STUDIO_FORCE_COPY=1
```

## 8. Fronteira única do Studio

`ArmoredStudio/unified.py`:

1. recebe item;
2. usa original imutável como fonte canônica;
3. reutiliza working somente se existir;
4. executa análise;
5. cria plano;
6. valida plano;
7. executa processamento;
8. valida resultado;
9. devolve working/result ao Core.

Não existem mais `modules/v1` ou `modules/v2` como arquitetura do Studio.

## 9. Pipeline sequencial

Estados principais:

```
RECEIVED
   ↓
VISION
   ↓
STUDIO
   ↓
PUBLISHING
   ↓
PUBLISHED
   ↓
CLEANUP
   ↓
DONE
```

Recovery também pode passar pelo estado RECOVERY e FAILED.

O Pipeline nunca processa dois itens simultaneamente.

## 10. Hub e publicação

Hub publica o resultado diretamente no Telegram destino.

A publicação é idempotente.

Antes de publicar, o sistema verifica se já existe publicação confirmável.

```
CONFIRMED → PUBLISHED
ABSENT     → pode publicar
UNKNOWN    → para; não república automaticamente
```

A confirmação pode usar o ID publicado ou o casamento determinístico do artefato final + affiliate URL.

## 11. Recovery

Recovery reconcilia:

- SQLite;
- filesystem;
- Telegram.

Casos:

```
STUDIO + working existente
    → continua Studio

STUDIO + working ausente + original existente
    → reconstrói a partir do original

PUBLISHING + resultado existente
    → verifica Telegram

PUBLISHING + publicação Telegram confirmada
    → PUBLISHED

PUBLISHED
    → nunca republica
    → cleanup

UNKNOWN
    → não republica automaticamente
```

Original sempre precisa existir para recuperação.

## 12. Reinício

Startup:

```
Coordinator
    ↓
Recovery
    ↓
pendências
    ↓
CATCH-UP se necessário
    ↓
LIVE quando histórico terminar
```

Se cair durante CATCH-UP, mensagens já vistas podem ser reencontradas. A restrição única do SQLite e o ID Telegram tornam a ingestão idempotente.

Se cair durante LIVE, os checkpoints por tópico permitem recuperar mensagens surgidas durante a indisponibilidade.

## 13. Portabilidade

Nenhum código pode depender de:

```
C:\Users\...
Desktop
Documents
Downloads
outro checkout
```

Todos os caminhos devem ser relativos à raiz do projeto ou configurados por ambiente.

Credenciais ficam fora do Git.

## 14. O que não faz parte da arquitetura

Não recriar como filas físicas:

```
storage/sync/
storage/queue/
storage/publish_queue/
storage/pipeline/
storage/hub/
storage/products/
storage/generated/
storage/rejected/
storage/archive/
storage/input/
storage/output/
storage/temp/
```

Diferença em relação ao backup não significa automaticamente que um módulo está faltando.

O backup é referência de **comportamento**, não de estrutura física.

## 15. Auditoria do backup

Comportamentos preservados:

1. coleta histórica;
2. monitoramento posterior;
3. vídeo + Shopee na mesma mensagem;
4. vídeo + Shopee na mensagem seguinte;
5. original permanente;
6. Vision antes do Studio;
7. Studio com análise e processamento;
8. RVC dentro do Studio;
9. publicação Telegram;
10. recuperação;
11. execução sequencial.

A reconstrução muda a organização física para centralizar estado e execução no Core/SQLite.

## 16. Registro definitivo de reconstrução e validação

Esta seção substitui todos os registros históricos abaixo do título anterior. O objetivo é registrar exatamente o que existe no HEAD atual, o que foi preservado/recriado/implementado/removido e o que já foi realmente testado.

### 16.1 Estado atual

- Branch: refactor/closure-batch
- HEAD: 1e5285b61da64760ca39ed4ec9474277dd47113e
- Último teste local confirmado: 58 passed, 1 skipped in 20.47s
- Repositório oficial: armoredcreator/armoredcreator — intocado
- Baseline oficial: audit/baseline-2026-09-19 @ dbea0b609040d1635ef7002f7144fecd531ec0b4

### 16.2 Classificação da reconstrução

PRESERVADO significa comportamento funcional vindo do sistema anterior/backup.

RECRIADO significa que o comportamento foi reconstruído em uma arquitetura nova.

IMPLEMENTADO significa capacidade nova necessária para tornar o pipeline determinístico, recuperável ou operacional.

ALTERADO significa que a implementação foi deliberadamente modificada para corrigir um defeito.

REMOVIDO significa que uma arquitetura antiga, duplicada ou insegura deixou de existir.

TESTADO significa que há cobertura automatizada ou uma validação real documentada.

NÃO CERTIFICADO significa que o código existe, mas a operação real correspondente ainda precisa ser executada.

---

## 17. Mapa módulo por módulo

### 17.1 ArmoredSync

Responsabilidade: descobrir e materializar candidatos. Não processa, não publica e não cria fila física.

PRESERVADO:
- CATCH-UP histórico;
- monitoramento LIVE;
- identidade pelo Telegram message ID;
- vídeo + Shopee na mesma mensagem;
- vídeo + Shopee na mensagem imediatamente seguinte quando a seguinte não é vídeo.

RECRIADO:
- ingestão centralizada pelo SQLite;
- materialização direta no workspace canônico;
- deduplicação por Telegram ID;
- checkpoints persistentes.

IMPLEMENTADO:
- materializer assíncrono;
- arquivo .part durante download;
- rollback da reserva quando a materialização falha;
- não baixar novamente quando o original já existe;
- CATCH-UP em lote;
- LIVE em lote.

ALTERADO:
- a coleta histórica deixou de desconectar/reconectar a cada item;
- todos os candidatos do lote são coletados/materializados enquanto a sessão Telegram permanece conectada.

TESTADO:
- regra de candidato;
- ingestão;
- deduplicação;
- checkpoints;
- lifecycle da sessão;
- CATCH-UP em lote;
- LIVE em lote;
- restart sem rematerialização do original.

### 17.2 Database / SQLite

Responsabilidade: fonte de verdade operacional.

PRESERVADO:
- identidade do conteúdo;
- estado;
- URLs;
- caminhos;
- tentativas;
- erros.

RECRIADO:
- schema centralizado;
- state_events;
- sync_topics;
- publication ledger.

IMPLEMENTADO:
- idempotency_key;
- published_message_id;
- verification_status;
- destination_chat_id;
- destination_topic_id;
- verified_at;
- recovery_count;
- cleanup_completed;
- SHA-256 do original.

ALTERADO:
- migração de schema corrigida;
- fechamento SQLite tornou-se seguro quando existe outra conexão durante shutdown;
- WAL não é forçado de volta para DELETE.

TESTADO:
- migração;
- schema legado;
- persistência;
- checkpoints;
- eventos;
- fechamento.

### 17.3 Coordinator

Arquivo: armored_core/coordinator.py

É a única raiz de composição.

Funções principais:

Coordinator.build():
- resolve root;
- carrega ambientes;
- cria Database;
- cria Sync;
- seleciona Telegram real ou fonte de teste;
- cria Pipeline;
- cria Recovery.

run_catch_up_async():
- coleta lote;
- materializa lote;
- libera a fonte;
- persiste checkpoints;
- processa itens sequencialmente.

run_catch_up():
- wrapper síncrono do CATCH-UP.

run_live_once_async():
- coleta lote LIVE;
- materializa antes do disconnect;
- libera Sync;
- processa um item por vez;
- confirma checkpoint somente após processamento do lote;
- não reprocessa FAILED automaticamente.

run_live_once():
- wrapper síncrono do LIVE.

recover_pending():
- recupera estados recuperáveis no startup;
- FAILED fica fora do retry automático.

recover(item_id):
- executa Recovery explícito.

run_forever():
- adquire runtime lock;
- faz recovery de startup;
- executa CATCH-UP quando necessário;
- entra em LIVE;
- permanece em polling;
- usa o mesmo Pipeline para itens novos.

close():
- fecha Database;
- libera recursos;
- libera runtime lock.

TESTADO:
- composição;
- CATCH-UP;
- LIVE;
- restart;
- deduplicação;
- runtime lock;
- falha em CATCH-UP;
- falha em LIVE.

### 17.4 Runtime lock

PRESERVADO/RECRIADO:
- exclusividade de um Coordinator ativo.

IMPLEMENTADO:
- recuperação de lock órfão;
- liberação em shutdown;
- liberação após exceção operacional.

TESTADO:
- segundo Coordinator é bloqueado;
- lock morto é recuperado;
- lock é removido após falha CATCH-UP;
- lock é removido após falha LIVE.

### 17.5 Pipeline

Responsabilidade: executar exatamente uma unidade de trabalho por vez.

Fluxo:

RECEIVED → VISION → STUDIO → PUBLISHING → PUBLISHED → CLEANUP → DONE

RECOVERY e FAILED são estados auxiliares.

PRESERVADO:
- ordem Vision → Studio → Hub.

RECRIADO:
- transições persistidas;
- cleanup como etapa explícita.

IMPLEMENTADO:
- regra de um item ativo;
- reutilização de artefatos duráveis durante Recovery.

TESTADO:
- ordem;
- transições;
- cleanup;
- publicação;
- falhas;
- recovery.

### 17.6 ArmoredVision

Responsabilidade:
- resolver URL Shopee;
- obter dados do produto;
- obter/generar affiliate URL;
- registrar affiliate_name;
- registrar affiliate_url.

PRESERVADO:
- Vision ocorre antes do Studio.

RECRIADO:
- integração como etapa do Pipeline.

REMOVIDO:
- qualquer dependência de fila física.

TESTADO:
- integração por testes do Pipeline/Core;
- fluxo de falha;
- persistência dos dados necessários.

A operação E2E real desde Telegram fonte ainda precisa ser certificada em um único teste contínuo.

### 17.7 ArmoredStudio

Arquitetura final:

ArmoredStudio/
- analysis/
- processing/
- runtime/
- unified.py

REMOVIDO:
- modules/v1 como arquitetura;
- modules/v2 como arquitetura;
- composição duplicada do Studio.

RECRIADO:
- uma única fronteira de processamento.

IMPLEMENTADO:
- unified.py;
- plano de exportação;
- validação do plano;
- execução;
- validação do resultado;
- integração interna de RVC.

### 17.8 ArmoredStudio/analysis

Componentes atuais:

- video.py — análise estrutural do vídeo;
- blackbar.py — análise de barras pretas;
- banner_analyzer.py — análise de condições do banner;
- banner.py — lógica de banner;
- veo_detector.py — detecção relacionada ao fluxo VEO;
- gemini_detector.py — detecção relacionada à marca/elemento Gemini;
- export_planner.py — construção do plano de exportação;
- export_plan_validator.py — validação do plano;
- export_executor.py — execução do plano;
- logger.py — logging da análise.

PRESERVADO:
- responsabilidades de análise existentes no Studio anterior.

RECRIADO:
- essas responsabilidades dentro de uma única arquitetura.

### 17.9 ArmoredStudio/processing

Componentes:

- rvc.py;
- tool_paths.py;
- finalizer.py.

rvc.py:
- RVC é interno ao Studio;
- localiza runtime/modelo;
- executa transformação de voz;
- retorna artefato para o fluxo.

tool_paths.py:
- centraliza descoberta de ferramentas;
- evita caminho absoluto da máquina.

finalizer.py:
- montagem/exportação final;
- preserva as regras de composição do Studio.

Assets preservados:
- ArmoredStudio/assets/banner.png;
- ArmoredStudio/assets/efeitosonoro.wav.

IMPLEMENTADO:
- cópia somente para laboratório via ARMORED_STUDIO_ALLOW_COPY=1 / ARMORED_STUDIO_FORCE_COPY=1;
- processamento real não deve cair silenciosamente para cópia.

### 17.10 ArmoredStudio/runtime/rvc

É runtime local/heavy e não arquitetura separada.

Modelo real utilizado na validação:
- melody.pth;
- melody_v2.index.

A máquina de validação atual não possui GPU Nvidia suportada; o runtime pode utilizar CPU.

O runtime pesado não deve ser tratado como módulo Python versionado.

### 17.11 ArmoredHub

Responsabilidade:
- registrar tentativa de publicação;
- garantir idempotência;
- publicar resultado;
- persistir message_id;
- verificar;
- confirmar somente depois da verificação.

PRESERVADO:
- publicação Telegram no tópico 228.

RECRIADO:
- publication ledger.

IMPLEMENTADO:
- publication_started;
- publication_message_sent;
- publication_confirmed;
- destination chat/topic;
- crash window explícita para teste;
- verificação independente.

REGRA:
- CONFIRMED → PUBLISHED;
- ABSENT → publicação pode ocorrer;
- UNKNOWN → parar e não republicar.

### 17.12 Recovery

Responsabilidade: reconciliar SQLite + filesystem + Telegram.

Casos implementados:

STUDIO + working:
→ continuar Studio.

STUDIO sem working + original:
→ reconstruir.

PUBLISHING + result:
→ verificar publicação.

PUBLISHING + Telegram confirmado:
→ PUBLISHED.

PUBLISHED:
→ nunca republicar; cleanup.

UNKNOWN:
→ nunca republicar automaticamente.

FAILED:
→ recovery explícito/manual, não retry infinito.

Original ausente:
→ recuperação bloqueada.

TESTADO:
- recovery automatizado;
- FAILED + resultado durável;
- publicação real do item 557;
- cleanup pós-confirmação.

---

## 18. Storage e arquivos — contrato final

Workspace único:

storage/videos/{telegram_message_id}/

Artefatos:
- original = permanente;
- working = derivado;
- result = derivado.

Original é sempre preservado.

Result é removido somente depois de publicação confirmada e cleanup.

Não existem filas físicas.

Não recriar:
storage/sync/
storage/queue/
storage/publish_queue/
storage/pipeline/
storage/hub/
storage/products/
storage/generated/
storage/rejected/
storage/archive/
storage/input/
storage/output/
storage/temp/

---

## 19. Telegram e confirmação

A publicação usa Bot API para envio e MTProto para verificação independente.

A verificação exige:
- chat correto;
- tópico correto;
- caption exatamente igual ao affiliate_url;
- mídia de vídeo/documento;
- nome do arquivo quando Telegram o expõe.

A busca utiliza o tail do affiliate URL e depois aplica os critérios exatos.

Tópico real validado:
228.

A confirmação independente do item 557 foi realizada com:
- destination_chat_id = -1004341972306;
- destination_topic_id = 228;
- published_message_id = 772;
- verification_status = CONFIRMED.

---

## 20. Testes automatizados — resultado atual

Comando:

    python -m pytest -q

Resultado confirmado no HEAD 1e5285b:

    58 passed, 1 skipped in 20.47s

0 testes falharam.

O número 58 é o estado atual. Resultados antigos de 45/54/55/56 testes são históricos.

Coberturas relevantes:
- Database;
- migrations;
- Storage;
- Sync;
- CATCH-UP;
- LIVE;
- checkpoints;
- deduplicação;
- Pipeline;
- Coordinator;
- Recovery;
- Hub;
- publication ledger;
- runtime lock;
- process restart lab;
- arquitetura/launcher.

---

## 21. Testes de durabilidade e restart

### CATCH-UP

O teste de restart verifica que um original já materializado não é baixado novamente.

### LIVE

O teste verifica:
- batch;
- materialização antes do disconnect;
- processamento sequencial;
- checkpoint;
- deduplicação;
- restart.

### Process lab

tests/e2e/run_process_lab.py executa processos separados contra a mesma base.

Primeira execução:

    STATE=PUBLISHED
    CHECKPOINT=100
    PUBLICATIONS=['lab-live-1']

Segunda execução:

    STATE=PUBLISHED
    CHECKPOINT=100
    PUBLICATIONS=['lab-live-1']

Isso demonstra persistência entre processos e publicação única.

---

## 22. Teste real do item 557

Estado inicial:
- FAILED;
- working ausente;
- result durável.

Dry-run:
- Recovery chegou a PUBLISHING;
- ARMORED_HUB_DRY_RUN=1 bloqueou publicação;
- item não foi marcado PUBLISHED.

Execução real:
- ARMORED_HUB_DRY_RUN=0;
- Telegram publicou;
- message_id = 772;
- MTProto confirmou;
- tópico = 228;
- verification_status = CONFIRMED;
- state = PUBLISHED;
- cleanup_completed = True.

Workspace final:

    557/
    └── 557_7VFOfg3R52.mp4

Prova obtida:
- resultado durável foi reutilizado;
- Vision não foi repetido;
- Studio não foi repetido;
- publicação real ocorreu;
- ID real foi persistido;
- confirmação independente ocorreu;
- cleanup ocorreu somente depois da confirmação;
- original permaneceu;
- resultado derivado foi removido;
- nenhuma duplicação foi observada.

Limite:
este teste não certifica sozinho o caminho completo Telegram fonte → Sync → Vision → Studio → Hub → Telegram.

---

## 23. Correções arquiteturais importantes

### Migração SQLite

Commit:
0505ac9cfe7d14b9511562325f5f4dd23d5b2fae

Teste:
b4e002bcd3e8d3cdb963c9309c26d68efc0268b7

### CATCH-UP em lote

Commits principais:
d9a7660e6f022e42cf185db0685dc241062c47ea
0b12f4c404ac7ac23382d6cd5ca981c13e8dcd5
57fd8089ba2c3f53e39ac3795d5f8e5f63677c41
e1b7d7e3535ef17bb4de17f7111cf2c925b374fc0

### Conexão Telegram

a52dffb29d1bd283ca5e4b941f334844247aba0b
0d489b1410a3097fff966f8fcb5d6fbe97b8b3c0

### Process restart lab

f71c1c42af1de19f3cdc5133c58e90008c94d968
2c569e31f0c9c3179795894ee41926fd3afc8a19
35ba648c018d5caf84aca6db02c32b4a52c43adf

### Limpeza arquitetural

e7374398aeb97217c1c88f82b23bdb5def52f195
b07ac56e836f54bdd87540da0d75ff4ed900497c
85246cab83d893131f1b4b68abfce83ef050080
8b73144048472fcb907109db... 

### Segurança operacional

1e5285b61da64760ca39ed4ec9474277dd47113e

---

## 24. O que ainda falta — somente o fechamento operacional

A arquitetura não precisa ser reconstruída novamente.

Faltam quatro certificações principais:

1. Coordinator contínuo real;
2. restart real durante LIVE;
3. integração operacional START_ALL;
4. E2E completo Telegram fonte → destino.

Depois disso:
5. reset final do DB/storage;
6. CATCH-UP limpo;
7. LIVE limpo;
8. E2E final em ambiente limpo.

### Coordinator contínuo real

Executar processo real e observar:
- startup recovery;
- CATCH-UP;
- transição para LIVE;
- polling;
- item novo;
- mesmo Pipeline;
- exatamente um item ativo.

### Restart real

Executar:
- LIVE;
- receber/processar item;
- interromper processo;
- iniciar novamente;
- confirmar checkpoint;
- confirmar ausência de duplicata.

### START_ALL

Executar somente o launcher operacional:

    START_ALL.bat

Confirmar:
- Python;
- root;
- Coordinator;
- Telegram;
- shutdown;
- restart.

### E2E

Um item real deve atravessar:

    Telegram fonte
    → Sync
    → SQLite
    → Vision
    → Studio
    → RVC/processamento
    → Hub
    → Telegram 228
    → MTProto
    → PUBLISHED
    → cleanup

Depois deve existir um segundo item e ele só deve começar após o primeiro terminar.

---

## 25. Matriz final de estado

| Área | Estado |
|---|---|
| Arquitetura canônica | ✅ validada |
| Storage único | ✅ validado |
| SQLite como verdade | ✅ validado |
| Identidade Telegram ID | ✅ validada |
| CATCH-UP automatizado | ✅ validado |
| LIVE automatizado | ✅ validado |
| Checkpoints | ✅ validados |
| Deduplicação | ✅ validada |
| Pipeline sequencial | ✅ validado |
| Studio unificado | ✅ validado por auditoria/testes |
| RVC interno | ✅ implementado |
| Recovery determinístico | ✅ validado |
| Publication ledger | ✅ validado |
| Hub real | ✅ validado no caso 557 |
| MTProto real | ✅ validado no caso 557 |
| Cleanup pós-confirmação | ✅ validado no caso 557 |
| Runtime lock | ✅ validado |
| Process restart lab | ✅ validado |
| Coordinator contínuo real | 🔲 falta certificação |
| Restart real em LIVE | 🔲 falta certificação |
| START_ALL operacional | 🔲 falta certificação |
| E2E fonte → destino | 🔲 falta certificação |
| DB/storage limpos | 🔲 fazer somente após os testes acima |
| Baseline oficial | 🔒 intocado |

---

## 26. Laboratório atual

O DB/storage atual ainda contém material histórico usado para diagnóstico.

Não interpretar automaticamente como produção.

Itens conhecidos incluem:
- FAILED 530;
- FAILED 532;
- RECEIVED 544 com .part;
- histórico de publicações dry-run;
- item 557 já validado e limpo.

Enquanto os testes finais não terminarem:

- não apagar o DB;
- não apagar storage/videos;
- não apagar 530/532/544;
- não tratar dry-* como publicação real;
- não republicar itens sem reconciliação;
- não alterar a baseline oficial.

O backup temporário de auditoria é descartável e só deve ser removido depois do fechamento diagnóstico.

---

## 27. START_ALL e composição

Arquitetura final:

    START_ALL.bat
        ↓
    run_coordinator.py
        ↓
    Coordinator
        ↓
    Sync / Pipeline / Recovery

Não existem mais múltiplos launchers competindo pela composição.

START_ALL já está implementado e testado estruturalmente.

A certificação restante é executar o processo real e observar CATCH-UP → LIVE → restart → E2E.

---

## 28. Critério definitivo de encerramento

O projeto será considerado fechado quando, em ambiente limpo, for observado:

    START_ALL.bat
        ↓
    Coordinator
        ↓
    Telegram fonte
        ↓
    CATCH-UP
        ↓
    LIVE
        ↓
    novo conteúdo
        ↓
    SQLite
        ↓
    Vision
        ↓
    Studio
        ↓
    RVC quando aplicável
        ↓
    Hub
        ↓
    Telegram tópico 228
        ↓
    confirmação independente
        ↓
    PUBLISHED
        ↓
    cleanup
        ↓
    próximo item
        ↓
    restart
        ↓
    checkpoint/recovery
        ↓
    continuação sem duplicação

Devem ser demonstrados simultaneamente:
- exatamente um item ativo;
- workspace único;
- nenhum storage legado;
- nenhuma fila física;
- nenhum caminho fixo de máquina;
- original sempre preservado;
- resultado removido após confirmação;
- publicação idempotente;
- UNKNOWN nunca republica;
- restart preserva identidade;
- CATCH-UP e LIVE usam a mesma identidade;
- START_ALL inicia a composição;
- processo continua operacional após restart.

---

## 29. Registro para o próximo chat

Não reconstruir a arquitetura do zero.

Continuar de:

    repo = armoredcreator/armoredcreator-test
    branch = refactor/closure-batch
    HEAD = 1e5285b61da64760ca39ed4ec9474277dd47113e
    pytest = 58 passed, 1 skipped

Oficial:

    armoredcreator/armoredcreator
    audit/baseline-2026-09-19
    dbea0b609040d1635ef7002f7144fecd531ec0b4

Última prova real:

    item 557
    FAILED → Recovery → Telegram real
    message_id 772
    topic 228
    CONFIRMED
    PUBLISHED
    cleanup=True
    original preservado
    result removido

Próximo trabalho:

    1. Coordinator contínuo real
    2. restart real em LIVE
    3. START_ALL real
    4. E2E completo fonte → destino
    5. segundo item sequencial
    6. reset final
    7. CATCH-UP limpo
    8. LIVE limpo
    9. certificação final

---

## 30. Resumo técnico final desta fase

A reconstrução atual:

- preserva o comportamento necessário;
- centraliza a verdade no SQLite;
- usa filesystem apenas como projeção/runtime;
- mantém Telegram como realidade externa;
- possui uma única raiz de composição;
- processa exatamente um item por vez;
- usa um workspace por item;
- mantém o original;
- remove derivados após confirmação;
- possui publicação idempotente;
- persiste o Telegram message ID antes da confirmação;
- verifica publicação independentemente;
- nunca republica quando o estado é UNKNOWN;
- possui Recovery determinístico;
- possui CATCH-UP e LIVE no mesmo pipeline;
- possui checkpoints;
- possui restart lab;
- possui runtime lock;
- removeu a arquitetura Studio v1/v2;
- removeu launchers duplicados;
- eliminou dependências de caminhos da máquina;
- possui suíte verde.

Estado automatizado atual:

    58 passed
    1 skipped
    0 failed

O que falta é **certificação operacional real da composição contínua**, não uma nova reconstrução da arquitetura.
