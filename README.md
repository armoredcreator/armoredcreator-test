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

## 16. Testes e estado de validação

Esta seção é o **registro operacional do laboratório**. Ela distingue claramente entre código implementado, teste automatizado aprovado, teste real executado, teste ainda não executado e resultado de laboratório usado apenas para diagnóstico.

Nunca promover um teste antigo para "validado no HEAD atual" sem executá-lo novamente.

### 16.1 Estado do código no momento deste registro

**Branch de trabalho:** refactor/single-storage-pipeline

**HEAD atual:** d827ccfc9f9c9bf79ccfc32617e8cdafa93404e6

**Último commit:** test: align durable-result recovery with cleanup contract

Esse commit corrigiu o teste de Recovery de resultado durável para respeitar o contrato real de cleanup:
- o resultado derivado pode ser removido após publicação confirmada;
- o original imutável deve permanecer;
- o banco deve ser fechado antes do cleanup do diretório temporário;
- o teste verifica o estado final e os eventos de RECOVERY/PUBLISHING/PUBLISHED.

### 16.2 Suíte automatizada atual

A suíte local foi executada após a correção acima com:

    python -m pytest -q

**Resultado confirmado pelo usuário:**

    47 passed, 1 skipped in 13.90s

Portanto:
- 47 testes passaram;
- 1 teste foi marcado como skip;
- nenhum teste falhou;
- a regressão de migração SQLite está coberta;
- o Recovery de FAILED + resultado durável está coberto;
- o contrato de cleanup está coberto.

O resultado anterior de 45 passed, 1 skipped ficou obsoleto após a inclusão dos testes adicionais. Não usar os números antigos como estado atual.

### 16.3 Correção da migração SQLite

Foi encontrada uma regressão na migração da tabela publications: o código continha uma chamada PRAGMA inválida.

Correção aplicada em:

    armored_core/database.py

Commit:

    0505ac9cfe7d14b9511562325f5f4dd23d5b2fae
    fix: correct SQLite schema migration pragma

Depois foi criado um teste de regressão:

    tests/test_database_migration.py

Commit:

    b4e002bcd3e8d3cdb963c9309c26d68efc0268b7
    test: cover legacy publication schema migration

A migração também foi executada sobre o banco real do laboratório e terminou com:

    DB MIGRADO OK

O schema real passou a conter:

    content_id
    idempotency_key
    published_message_id
    confirmed
    created_at
    updated_at
    verification_status
    destination_chat_id
    destination_topic_id
    verified_at

Antes da migração foi criado um backup temporário do banco para permitir reversão durante a validação.

### 16.4 Recovery determinístico — teste automatizado

Foi criado:

    tests/test_failed_result_recovery.py

Cenário coberto:

    FAILED
      ↓
    resultado final já existente e durável
      ↓
    Recovery
      ↓
    não executa Vision
      ↓
    não executa Studio
      ↓
    PUBLISHING
      ↓
    Publisher
      ↓
    PUBLISHED
      ↓
    cleanup

O teste usa Vision/Studio que falham deliberadamente se forem chamados. Isso prova que, quando o resultado final já é durável, o Recovery pode reutilizá-lo diretamente.

O teste também confirma:
- publicação executada uma única vez;
- estado final PUBLISHED;
- resultado derivado removido pelo cleanup;
- original preservado;
- workspace contendo somente o original após cleanup;
- eventos de RECOVERY, PUBLISHING e PUBLISHED.

Esse teste foi corrigido depois de uma primeira versão que esperava que o resultado permanecesse no disco mesmo após cleanup. O contrato correto é remover o derivado após confirmação e preservar o original.

### 16.5 Recovery real do item 557

Este é o teste real mais importante executado até agora.

O item 557 existia no banco real do laboratório como:

    state = FAILED
    working_path = None
    result_path = storage/videos/557/557_9zxtYncz8J.mp4
    affiliate_url = https://s.shopee.com.br/9zxtYncz8J

Workspace inicial:

    557/
    ├── 557_7VFOfg3R52.mp4   ← original
    └── 557_9zxtYncz8J.mp4   ← resultado

Primeiro foi executado Recovery com:

    ARMORED_HUB_DRY_RUN=1

O Recovery reconheceu o resultado durável e chegou a PUBLISHING, mas o Hub bloqueou a publicação por segurança:

    RuntimeError:
    ARMORED_HUB_DRY_RUN=1:
    publicação real bloqueada;
    nenhum item pode ser marcado como PUBLISHED

Isso confirmou que o Recovery estava funcionando até a fronteira real de publicação.

Em seguida foi feita a mesma recuperação com:

    ARMORED_HUB_DRY_RUN=0

Resultado real:

    ANTES
    state: FAILED
    result: .../storage/videos/557/557_9zxtYncz8J.mp4

    DEPOIS
    state: PUBLISHED
    cleanup_completed: True
    recovery_count: 4
    attempts: 6

Publication real persistida:

    content_id: 557
    idempotency_key: armoredcreator:content:557
    published_message_id: 772
    confirmed: 1
    verification_status: CONFIRMED
    destination_chat_id: -1004341972306
    destination_topic_id: 228
    verified_at: 2026-09-20 21:48:20

Workspace após o processo:

    557/
    └── 557_7VFOfg3R52.mp4

O resultado derivado foi removido e o original permaneceu.

**Conclusão desta validação:** o Recovery real de um FAILED com resultado durável foi executado de ponta a ponta, com publicação Telegram real e confirmação real no tópico 228, sem reexecutar Vision/Studio.

### 16.6 O que o teste 557 realmente provou

O teste 557 comprovou:
- item FAILED pode entrar em Recovery manual;
- Recovery encontra resultado durável;
- Recovery não precisa reconstruir Vision/Studio quando o resultado existe;
- publicação real no Telegram funciona;
- ID real da mensagem é persistido;
- confirmação independente funciona;
- destino real é o chat -1004341972306;
- tópico real é 228;
- verification_status chega a CONFIRMED;
- confirmed chega a 1;
- estado chega a PUBLISHED;
- cleanup ocorre somente depois da confirmação;
- resultado derivado é removido;
- original permanece;
- não houve duplicação durante essa recuperação.

Esse teste é uma **validação real de Recovery + publicação**, mas não é ainda a certificação do Coordinator contínuo completo.

### 16.7 Estado do banco real usado no laboratório

Após os testes, o banco local contém uma mistura deliberada de estados históricos e resultados de testes. Isso é útil para diagnóstico, mas não representa o banco limpo final de produção.

No inventário executado após o teste 557:

    FAILED:    2
    PUBLISHED: 12
    RECEIVED:   1

Itens observados:

    530  FAILED
    532  FAILED
    536  PUBLISHED
    539  PUBLISHED
    542  PUBLISHED
    544  RECEIVED
    557  PUBLISHED
    558  PUBLISHED
    560  PUBLISHED
    563  PUBLISHED
    564  PUBLISHED
    706  PUBLISHED
    823  PUBLISHED
    1174 PUBLISHED
    1383 PUBLISHED

### 16.8 Publicações históricas e contaminação de teste

O inventário revelou registros que não podem ser tratados como publicação real:

    536  message=dry-536  confirmed=1  verification_status=PENDING
    539  message=dry-539  confirmed=1  verification_status=PENDING
    542  message=dry-542  confirmed=1  verification_status=PENDING
    558  message=dry-558  confirmed=1  verification_status=PENDING
    560  message=dry-560  confirmed=1  verification_status=PENDING
    563  message=dry-563  confirmed=1  verification_status=PENDING
    564  message=dry-564  confirmed=1  verification_status=PENDING
    706  message=dry-706  confirmed=1  verification_status=PENDING
    823  message=dry-823  confirmed=1  verification_status=PENDING
    1174 message=dry-1174 confirmed=1  verification_status=PENDING
    1383 message=771     confirmed=1  verification_status=PENDING

Esses registros são tratados como **diagnóstico de laboratório**, não como evidência de publicação real.

O único item explicitamente comprovado nesta etapa por publicação real e confirmação independente foi:

    557 → Telegram message 772 → CONFIRMED → PUBLISHED

Não assumir que qualquer confirmed=1 antigo significa Telegram real confirmado. A evidência confiável exige estado e verification coerentes e, quando necessário, reconciliação com Telegram.

### 16.9 Falhas históricas preservadas para diagnóstico

Os itens 530 e 532 permanecem como FAILED no laboratório.

Erros registrados anteriormente:

    530 = ShopeeAPIError: Produto não encontrado: 590473480:50603522807
    532 = ShopeeAPIError: Produto não encontrado: 319857762:22093051984

Eles não devem ser apagados enquanto ainda estivermos usando o banco atual para diagnóstico, mas também não devem ser confundidos com uma base limpa de produção.

O item 544 está em:

    RECEIVED

e possui um artefato parcial:

    544_5AqOWAutYS.mp4.part

Esse arquivo não deve ser tratado como vídeo finalizado.

### 16.10 Regra atual para FAILED

A exclusão de FAILED do Recovery automático no startup é intencional.

    Coordinator startup
        ↓
    recover_pending()
        ↓
    FAILED não entra automaticamente

A recuperação de FAILED é explícita:

    coordinator.recover(item_id)

Isso evita loops automáticos de erro e preserva a possibilidade de recuperação determinística/manual.

O teste real do 557 confirmou que essa recuperação explícita funciona.

### 16.11 Testes de operação contínua

Já existem testes cobrindo o contrato de:
- CATCH-UP → LIVE;
- checkpoints por tópico;
- deduplicação;
- múltiplas mensagens;
- processamento sequencial;
- restart;
- persistência da identidade;
- recuperação após falha.

Esses testes fazem parte da suíte automatizada atual e estão incluídos no resultado:

    47 passed, 1 skipped

Porém, **teste automatizado não equivale à certificação de operação real contínua do processo Windows**.

Ainda precisamos executar no ambiente real:

    START_COORDINATOR
        ↓
    CATCH-UP real
        ↓
    LIVE real
        ↓
    novo Telegram
        ↓
    processamento
        ↓
    publicação
        ↓
    restart real do processo
        ↓
    retomada por checkpoint

### 16.12 Bot / START_ALL

O laboratório já possui:

    run_coordinator.py
    START_COORDINATOR.bat

O START_COORDINATOR.bat resolve a raiz do projeto de maneira portátil e não depende de caminhos fixos de máquina.

Entretanto, a **integração final com o Bot histórico e o START_ALL de produção ainda não está certificada**.

Não declarar essa etapa como concluída somente porque o Coordinator isolado inicia.

### 16.13 E2E real completo

Existe suporte para E2E real e a publicação real já foi comprovada no item 557.

Entretanto, o teste 557 foi:

    FAILED existente
        ↓
    Recovery
        ↓
    resultado já produzido
        ↓
    Hub
        ↓
    Telegram destino

Ele **não cobre** todo o caminho:

    Telegram fonte
        ↓
    ArmoredSync CATCH-UP/LIVE
        ↓
    SQLite
        ↓
    Coordinator
        ↓
    Vision real
        ↓
    Studio real
        ↓
    Hub real
        ↓
    Telegram destino
        ↓
    confirmação
        ↓
    cleanup

Portanto:

**Recovery + publicação real: APROVADO.**

**E2E completo desde a descoberta do Telegram fonte até o destino: AINDA NÃO CERTIFICADO.**

### 16.14 Estado de validação atual

| Área | Estado |
|---|---|
| Arquitetura canônica | ✅ APROVADA por auditoria/testes |
| Storage único | ✅ APROVADO |
| SQLite como fonte de verdade | ✅ APROVADO |
| Identidade por Telegram ID | ✅ APROVADO |
| CATCH-UP/LIVE | ✅ APROVADO por testes automatizados |
| Deduplicação | ✅ APROVADA por testes |
| Pipeline sequencial | ✅ APROVADO por testes |
| ArmoredStudio unificado | ✅ APROVADO por auditoria |
| RVC interno ao Studio | ✅ IMPLEMENTADO |
| Migração SQLite | ✅ APROVADA |
| Recovery determinístico | ✅ APROVADO |
| FAILED + resultado durável | ✅ APROVADO em teste automatizado |
| FAILED + resultado durável + Telegram real | ✅ APROVADO no item 557 |
| Publicação Telegram real | ✅ COMPROVADA no item 557 |
| Confirmação MTProto real | ✅ COMPROVADA no item 557 |
| Cleanup pós-confirmação | ✅ COMPROVADO no item 557 |
| Original preservado | ✅ COMPROVADO no item 557 |
| Coordinator.run_forever implementado | ✅ IMPLEMENTADO |
| Coordinator contínuo com Telegram real | 🔲 NÃO CERTIFICADO |
| Restart real do processo em LIVE | 🔲 NÃO CERTIFICADO |
| Integração Bot/START_ALL | 🔲 NÃO CERTIFICADA |
| E2E real completo fonte → destino | 🔲 NÃO CERTIFICADO |
| Base local limpa | 🔲 AINDA NÃO — laboratório contém histórico/testes |
| Backup temporário de auditoria | 🟡 EXISTE APENAS COMO SEGURANÇA TEMPORÁRIA |
| Baseline oficial | 🔒 INTACTO |

### 16.15 Backup temporário e reset final

Durante a validação foi criado um backup temporário **fora do repositório** para preservar o DB e os vídeos do laboratório antes de qualquer eventual reset.

Esse backup é **descartável**. Ele não faz parte da arquitetura final e não deve ser mantido como dependência do projeto.

A ordem correta é:

    continuar usando o laboratório atual
        ↓
    fechar Recovery restante / Coordinator / Bot / E2E
        ↓
    executar inventário final
        ↓
    não precisar mais dos artefatos históricos
        ↓
    remover backup temporário
        ↓
    resetar DB + storage/videos
        ↓
    iniciar banco/storage limpos
        ↓
    executar CATCH-UP real
        ↓
    executar LIVE real
        ↓
    executar E2E final

**Não resetar o ambiente antes de terminar os testes diagnósticos que ainda possam se beneficiar dos dados atuais.**

### 16.16 Próximos passos oficiais

A partir deste registro, a ordem de trabalho é:

#### 1. Continuar usando o laboratório atual

Usar os itens históricos somente para descobrir e corrigir falhas de Recovery/idempotência.

Não considerar os estados históricos como produção limpa.

#### 2. Fechar Coordinator contínuo real

Validar:

    run_forever()
        ↓
    startup recovery
        ↓
    CATCH-UP
        ↓
    LIVE
        ↓
    polling contínuo
        ↓
    novo item
        ↓
    mesmo pipeline

#### 3. Validar restart real

Executar:

    processo ativo
        ↓
    LIVE
        ↓
    novo item/checkpoint
        ↓
    interrupção real
        ↓
    restart
        ↓
    checkpoint
        ↓
    continuação sem duplicata

#### 4. Fechar Bot / START_ALL

Validar o processo real que será usado para iniciar a composição completa.

Não considerar START_COORDINATOR.bat isoladamente como substituto da integração final.

#### 5. E2E completo real

Validar pelo menos um conteúdo real desde:

    Telegram fonte
    → Sync
    → SQLite
    → Vision
    → Studio
    → Hub
    → Telegram destino
    → confirmação
    → cleanup

e confirmar que o segundo item entra somente depois do primeiro terminar.

#### 6. Teste final limpo

Somente quando tudo acima estiver fechado:

    backup temporário → remover
    DB atual → resetar
    storage/videos atual → resetar
    artefatos de teste → remover

Então:

    CATCH-UP limpo
    → LIVE
    → E2E real
    → restart
    → recovery
    → segundo item
    → publicação
    → cleanup

Esse será o teste de certificação final da composição.

## 17. Ordem oficial desta fase

### Fase 1 — CATCH-UP

1. coleta histórica real;
2. todos os tópicos;
3. candidatos válidos;
4. download direto para workspace;
5. deduplicação;
6. SQLite;
7. nenhuma cópia intermediária;
8. detectar fim do histórico;
9. persistir CATCH-UP → LIVE.

### Fase 2 — LIVE

10. monitoramento contínuo;
11. novas mensagens;
12. mesma regra de candidato;
13. checkpoints por tópico;
14. recuperação após parada;
15. deduplicação histórico/LIVE;
16. novos itens no mesmo SQLite.

### Fase 3 — Core

17. Sync → DB → Coordinator → Pipeline;
18. um item ativo;
19. Vision;
20. Studio;
21. Hub;
22. confirmação Telegram;
23. PUBLISHED;
24. cleanup.

### Fase 4 — Recovery

25. queda durante CATCH-UP;
26. queda durante LIVE;
27. queda durante Vision;
28. queda durante Studio;
29. queda após publicação;
30. nenhuma republicação indevida;
31. publicação UNKNOWN nunca gera republicação automática;
32. resultado durável é reutilizado quando seguro.

### Fase 5 — Operação contínua

33. Coordinator real em run_forever();
34. CATCH-UP real;
35. LIVE real;
36. restart real;
37. retomada por checkpoint;
38. processamento sequencial após restart.

### Fase 6 — Integração operacional

39. Bot;
40. START_ALL;
41. inicialização da composição completa;
42. shutdown/restart;
43. recuperação automática.

### Fase 7 — E2E real

44. Telegram fonte real;
45. Sync real;
46. Vision real;
47. Studio/RVC real;
48. Hub real;
49. Telegram destino real;
50. confirmação real;
51. cleanup;
52. próximo item.

### Fase 8 — Reset final

53. fechar todos os testes diagnósticos;
54. remover backups temporários;
55. resetar DB;
56. resetar storage/videos;
57. preservar somente a estrutura canônica;
58. CATCH-UP limpo;
59. LIVE limpo;
60. E2E final;
61. restart final;
62. Recovery final.

Somente depois dessa sequência a composição poderá ser considerada encerrada.

## 18. Estado atual de fechamento

### FECHADO / VALIDADO

- ✅ Arquitetura canônica.
- ✅ Storage único.
- ✅ SQLite como fonte de verdade.
- ✅ CATCH-UP/LIVE por testes automatizados.
- ✅ Deduplicação.
- ✅ Pipeline sequencial.
- ✅ ArmoredStudio unificado.
- ✅ RVC interno.
- ✅ Migração SQLite.
- ✅ Recovery determinístico.
- ✅ Recovery de FAILED + resultado durável.
- ✅ Publicação Telegram real.
- ✅ Confirmação MTProto real.
- ✅ Cleanup pós-publicação.
- ✅ Preservação do original.
- ✅ Caso real 557 completo de Recovery até Telegram confirmado.
- ✅ Suíte automatizada: **47 passed, 1 skipped**.

### IMPLEMENTADO, MAS AINDA NÃO CERTIFICADO EM OPERAÇÃO CONTÍNUA REAL

1. 🔲 Coordinator contínuo real em processo longo.
2. 🔲 Restart real durante LIVE.
3. 🔲 Integração final Bot/START_ALL.
4. 🔲 E2E completo desde Telegram fonte até Telegram destino.
5. 🔲 Teste final com DB/storage completamente limpos.

### LABORATÓRIO ATUAL

O ambiente local ainda contém dados históricos deliberadamente preservados para diagnóstico:
- itens FAILED;
- item RECEIVED;
- publicações dry-run;
- publicações históricas que ainda precisam de reconciliação;
- arquivos antigos;
- o caso real 557 já concluído.

Isso **não é considerado o estado final**.

O reset só será feito quando os testes diagnósticos restantes estiverem encerrados.

## 19. Critério final de encerramento

A reconstrução será considerada operacionalmente fechada somente quando o seguinte fluxo tiver sido executado e observado em ambiente limpo:

    Telegram fonte
           ↓
    ArmoredSync
           ↓
    SQLite
           ↓
    Coordinator contínuo
           ↓
    Vision
           ↓
    ArmoredStudio / RVC
           ↓
    ArmoredHub
           ↓
    Telegram destino / tópico 228
           ↓
    confirmação independente
           ↓
    PUBLISHED
           ↓
    cleanup
           ↓
    próximo item
           ↓
    LIVE contínuo
           ↓
    restart
           ↓
    checkpoint/recovery
           ↓
    continuação sem duplicação

O teste deve demonstrar simultaneamente:
- exatamente um item ativo;
- nenhuma fila física;
- nenhum storage legado;
- nenhum caminho dependente da máquina;
- nenhum processamento paralelo;
- nenhum resultado derivado preservado indevidamente;
- original sempre preservado;
- nenhuma republicação após confirmação;
- UNKNOWN bloqueia republicação;
- restart não perde identidade;
- CATCH-UP e LIVE compartilham a mesma identidade;
- Bot/START_ALL inicia a composição real;
- o processo permanece operacional após reinício.

## 20. Registro para continuidade em outro chat

Se este chat atingir o limite, o próximo chat deve continuar **a partir desta seção e do HEAD indicado**, sem reconstruir o histórico do zero.

### Referência fixa

    REPOSITÓRIO DE TRABALHO:
    armoredcreator/armoredcreator-test

    BRANCH:
    refactor/single-storage-pipeline

    HEAD REGISTRADO:
    d827ccfc9f9c9bf79ccfc32617e8cdafa93404e6

    REPOSITÓRIO OFICIAL:
    armoredcreator/armoredcreator

    REGRA:
    NÃO ALTERAR O REPOSITÓRIO OFICIAL

    BASELINE OFICIAL:
    audit/baseline-2026-09-19
    commit dbea0b609040d1635ef7002f7144fecd531ec0b4

### Último teste real decisivo

    ITEM:
    557

    ANTES:
    FAILED

    RESULTADO DURÁVEL:
    557_9zxtYncz8J.mp4

    RECOVERY:
    executado

    VISION:
    não executado novamente

    STUDIO:
    não executado novamente

    PUBLICAÇÃO:
    Telegram real

    MESSAGE_ID:
    772

    DESTINO:
    -1004341972306

    TÓPICO:
    228

    VERIFICAÇÃO:
    CONFIRMED

    ESTADO FINAL:
    PUBLISHED

    CLEANUP:
    True

    ORIGINAL:
    preservado

    RESULTADO:
    removido após confirmação

### Última suíte automatizada

    47 passed, 1 skipped in 13.90s

### Próxima tarefa

**Não começar novamente pela arquitetura.**

Continuar nesta ordem:

    1. Coordinator contínuo real
    2. restart real durante LIVE
    3. integração Bot / START_ALL
    4. E2E completo Telegram fonte → destino
    5. somente então reset final DB/storage
    6. teste final totalmente limpo

### Regra do laboratório

Enquanto os testes diagnósticos ainda estiverem sendo executados:

    NÃO apagar DB atual
    NÃO apagar storage/videos
    NÃO apagar 530/532/544
    NÃO apagar histórico de state_events
    NÃO tratar dry-* como publicação real
    NÃO publicar novamente itens sem reconciliação
    NÃO alterar audit/baseline-2026-09-19

O laboratório atual existe para diagnóstico temporário. Quando o fechamento estiver concluído, todos os dados de teste/backup serão descartados e o projeto será validado novamente a partir de uma base limpa.

## 21. Próxima otimização

Agora a prioridade continua sendo **correção, recuperação, operação contínua e fechamento E2E**, não performance.

Depois da certificação CATCH-UP → LIVE + E2E, pode ser avaliada a fusão de crop_final/cortes temporais no filter graph do finalizer para evitar uma segunda passada FFmpeg quando possível.

## Regra de segurança

Nunca alterar:

    audit/baseline-2026-09-19

Todo trabalho desta fase permanece em:

    refactor/single-storage-pipeline
