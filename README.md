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

Esta seção registra **somente validações realmente executadas**. O README é a referência rápida para saber o que estava aprovado em cada etapa; resultados não executados não são tratados como aprovados.

### Última validação local confirmada

**Commit:** `1f3f85f`  
**Branch:** `refactor/single-storage-pipeline`  
**Working tree após o commit:** limpa

Suite completa:

```
python -m pytest -q

27 passed
```

E2E real do Telegram:

```
$env:ARMORED_REAL_TELEGRAM_E2E="1"
python -m pytest tests\e2e\test_real_telegram.py -v -s

1 passed
```

### Estado aprovado

| Área | Estado |
|---|---|
| Suite automatizada | ✅ APROVADO — 27 passed |
| E2E real Telegram | ✅ APROVADO — 1 passed |
| Fluxo Sync → Vision → Studio → Hub → Telegram | ✅ APROVADO no E2E real |
| CATCH-UP → LIVE | ✅ APROVADO nos testes de lifecycle |
| Deduplicação histórico/LIVE | ✅ APROVADO nos testes |
| Recovery determinístico | ✅ APROVADO nos testes |
| Assets do Studio | ✅ VERSIONADOS — `banner.png` e `efeitosonoro.wav` |
| Baseline oficial | 🔒 INTACTO |
| Otimização de performance RVC/FFmpeg | 🔲 NÃO É OBJETIVO DESTA FASE |

### Como repetir a validação

```powershell
cd C:\Users\Administrador\Downloads\ArmoredCreator
git pull origin refactor/single-storage-pipeline

python -m pytest -q

$env:ARMORED_REAL_TELEGRAM_E2E="1"
python -m pytest tests\e2e\test_real_telegram.py -v -s
```

### Auditoria arquitetural adicionada

Foi adicionado `tests/test_architecture_cleanup.py` para bloquear regressões em pontos críticos:

- storage legado;
- filas físicas antigas;
- arquitetura `ArmoredStudio/modules/v1` e `ArmoredStudio/modules/v2`;
- caminhos Windows dependentes de uma máquina específica.

**Estado desta nova auditoria:** 🔲 NÃO VALIDADO LOCALMENTE após a criação do teste. O número `27 passed` acima continua sendo o último resultado realmente executado e aprovado.

### Histórico de resultados

Resultados anteriores registrados no laboratório:

- `22 passed` — etapa anterior, antes do fechamento do lifecycle CATCH-UP → LIVE.
- `27 passed` — suite atual após o lifecycle.
- `1 passed` — E2E real Telegram executado com `ARMORED_REAL_TELEGRAM_E2E=1`.

**Regra:** quando uma alteração mudar o comportamento, esta seção deve ser atualizada somente após executar os testes correspondentes. Registrar separadamente `APROVADO`, `SKIP`, `FALHOU` e `NÃO VALIDADO`.

Testes obrigatórios desta fase:

```
test_sync_historical_collection
test_sync_historical_dedup
test_sync_historical_finishes
test_sync_transitions_to_live
test_sync_detects_new_message
test_new_message_enters_pipeline
test_multiple_new_messages_are_sequential
test_restart_does_not_duplicate
test_history_and_live_share_same_identity
```

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
30. nenhuma republicação indevida.

### Fase 5 — E2E real

31. histórico Telegram real;
32. transição CATCH-UP → LIVE;
33. novo vídeo real;
34. pipeline completo;
35. publicação real;
36. confirmação;
37. cleanup;
38. item seguinte.

Somente depois disso o fluxo Sync será considerado fechado.

## 18. Próxima otimização

Agora a prioridade é **correção e fechamento do fluxo**, não performance.

Depois de CATCH-UP → LIVE + E2E, pode ser avaliada a fusão de `crop_final`/cortes temporais no filter graph do finalizer para evitar uma segunda passada FFmpeg quando possível.

## Regra de segurança

Nunca alterar:

```
audit/baseline-2026-09-19
```

Todo trabalho desta fase permanece em:

```
refactor/single-storage-pipeline
```
