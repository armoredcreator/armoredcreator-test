# ArmoredCreator — Arquitetura Canônica e Certificação

> **Laboratório isolado:** este repositório é o ambiente de reconstrução e certificação do ArmoredCreator.  
> O repositório oficial `armoredcreator/armoredcreator` e a branch `audit/baseline-2026-09-19` permanecem intocados.

## 1. Estado do projeto

Este README descreve o estado do código no **main após a correção do LIVE Telegram polling** e é a referência operacional para a próxima etapa: **CATCH-UP histórico completo**.

### Marco atual

- **Repository:** `armoredcreator/armoredcreator-test`
- **Branch operacional:** `main`
- **Última correção funcional antes deste README:** PR #14 — redução do polling LIVE e dos `FloodWait`
- **Fluxo real já comprovado:** Telegram fonte → Sync → SQLite → Vision → Studio/RVC → Hub → Telegram destino → confirmação → cleanup.
- **CATCH-UP → LIVE real:** comprovado com 3 itens reais.
- **Restart em LIVE:** comprovado.
- **Wi-Fi OFF/ON + reconexão Telegram:** comprovado, incluindo reconstrução da sessão e ausência do antigo `database is locked`.
- **CATCH-UP histórico completo dos ~308 conteúdos:** ainda não executado nesta certificação limpa.
- **Novo vídeo inserido manualmente no grupo fonte:** não reproduzível, porque a fonte pertence a terceiros.

### Regra de congelamento

A partir do commit de congelamento desta documentação, **não modificar o código da pipeline antes de terminar a certificação do CATCH-UP histórico**, salvo correção bloqueadora descoberta durante o próprio teste.

O objetivo desta etapa é medir a operação da arquitetura existente, não continuar reconstruindo a arquitetura durante a medição.

---

# 2. Arquitetura definitiva

O ArmoredCreator é composto por fronteiras de serviço, mas possui **uma única raiz de composição operacional**: o Coordinator.

Fluxo:

```
Telegram fonte
      │
      ▼
ArmoredSync
      │
      ▼
SQLite + workspace canônico
storage/videos/{item_id}/
      │
      ▼
Coordinator
      │
      ├──► ArmoredVision
      │
      ├──► ArmoredStudio
      │      ├── análise
      │      ├── processamento
      │      ├── RVC
      │      └── FFmpeg
      │
      └──► ArmoredHub
               │
               ▼
       Telegram destino / tópico
               │
               ▼
       confirmação determinística
               │
               ▼
            PUBLISHED
               │
               ▼
             cleanup
               │
               ▼
          próximo item
```

A regra central é:

> **um único item ativo por vez e nenhuma materialização antecipada de lote.**

Exemplo:

```
descobrir A
→ materializar A
→ Vision
→ Studio
→ Hub
→ confirmar
→ cleanup
→ só então descobrir B
```

Isso vale tanto para **CATCH-UP** quanto para **LIVE**.

---

# 3. Fontes de verdade

O sistema não depende de memória do processo para reconstruir o estado.

### SQLite

Verdade interna e persistente:

```
storage/database/armoredcreator.db
```

Contém identidade, estado, eventos, publicações, checkpoints e dados necessários à recuperação.

### Filesystem

Projeção física do item:

```
storage/videos/{item_id}/
```

O original é permanente. Working/result são artefatos de processamento e recuperação.

### Telegram

Verdade externa da publicação.

Uma publicação só pode ser considerada confirmada de acordo com a regra de confirmação registrada em SQLite + verificação Telegram.

### Coordinator

É a autoridade de composição e sequência. Não é uma fila paralela, nem um quinto banco de estado.

---

# 4. ArmoredSync

Arquivo principal:

```
ArmoredSync/service.py
```

Responsabilidade:

- conectar ao Telegram fonte;
- descobrir tópicos;
- localizar candidatos históricos;
- localizar candidatos LIVE;
- materializar o vídeo diretamente no workspace canônico;
- preservar `telegram_message_id`;
- controlar checkpoints;
- liberar a sessão antes do Hub quando necessário.

O Sync **não executa Vision, Studio ou Hub**.

## 4.1 Identidade do candidato

A identidade canônica é:

```
telegram_message_id
```

Cada item ingerido deriva dessa identidade.

## 4.2 Regra vídeo + Shopee

Um candidato é aceito quando:

1. o vídeo e o link Shopee estão na mesma mensagem; ou
2. existe um vídeo e a **mensagem imediatamente seguinte** contém o link Shopee.

Não existe busca arbitrária por links várias mensagens depois.

## 4.3 CATCH-UP

No CATCH-UP histórico, a descoberta é progressiva.

O Sync não baixa 10, 50 ou 300 vídeos para uma pasta intermediária.

O contrato é:

```
candidato 1
→ materializa
→ libera Sync
→ processamento completo
→ confirmação
→ cleanup

candidato 2
→ materializa
→ ...
```

O checkpoint histórico só deve avançar depois que o item anterior passou pelo ciclo necessário.

## 4.4 LIVE

O LIVE usa o mesmo contrato de item único.

Depois da correção de polling, ele:

- consulta **um tópico por ciclo**;
- alterna os tópicos em rodízio;
- usa o checkpoint persistido daquele tópico;
- examina no máximo as mensagens necessárias para a regra vídeo + próxima mensagem;
- não varre novamente os 16 tópicos a cada 2 segundos.

Isso evita sobrecarga desnecessária e reduz a chance de `FloodWait`.

### Importante sobre FloodWait

`FloodWaitError` é uma resposta de rate-limit do Telegram. Não deve ser interpretada como perda de Wi-Fi.

O watchdog `ARMORED_LIVE_DISCOVERY_TIMEOUT` existe para impedir que uma descoberta de rede realmente travada prenda o LIVE indefinidamente, mas **não é um timeout de processamento de vídeo** e não limita RVC/FFmpeg/Studio.

---

# 5. TelegramReader e lifecycle da sessão

Dentro de `ArmoredSync/service.py`, `TelegramReader` é a camada que possui a sessão Telethon do Sync.

Sessão canônica:

```
storage/credentials/telegram/session/armoredsync
```

Quando a conexão precisa ser reconstruída:

1. o client anterior é desconectado;
2. a sessão SQLite do Telethon é fechada;
3. um novo client é criado;
4. o login/conexão é refeito;
5. a reconexão é registrada no log.

Isso corrige o cenário Windows em que dois objetos Telethon poderiam manter a mesma SQLite session aberta e gerar:

```
sqlite3.OperationalError: database is locked
```

A reconexão real foi testada desligando e religando o Wi-Fi.

Log esperado:

```
[SYNC][TELEGRAM] sessão Telegram conectada/reconectada
```

---

# 6. armored_core

O diretório `armored_core/` contém o núcleo da arquitetura.

## `models.py`

Define os estados e estruturas canônicas.

Estados:

```
RECEIVED
VISION
WAITING_VISION
STUDIO
PUBLISHING
PUBLISHED
RECOVERY
FAILED
```

Verificações de publicação:

```
CONFIRMED
ABSENT
UNKNOWN
```

A classe `Item` transporta a identidade e os paths canônicos de cada conteúdo.

`item_id` existe como alias compatível, mas a identidade canônica é `content_id`.

## `database.py`

Responsável pelo SQLite:

- criação/migração do schema;
- items;
- eventos;
- publications;
- checkpoints;
- runtime lock;
- consultas de estado;
- persistência das decisões.

Local:

```
storage/database/armoredcreator.db
```

## `storage.py`

Resolve paths físicos da arquitetura.

Responsabilidade:

- root portátil;
- database;
- workspaces;
- logs;
- backups;
- paths de item.

Não cria filas físicas antigas.

## `services.py`

Contém os contratos/serviços centrais entre as etapas:

- ingestão;
- Vision result;
- Studio result;
- publication result;
- exceções específicas.

É a ponte de contrato do Core para os adapters reais.

## `pipeline.py`

Executa o fluxo sequencial de um item:

```
RECEIVED
→ VISION
→ STUDIO
→ PUBLISHING
→ PUBLISHED
```

Cleanup ocorre de forma controlada após publicação confirmada.

O pipeline não cria concorrência de itens.

## `recovery.py`

Faz a reconciliação de item usando:

```
SQLite
+
filesystem
+
estado de publicação
```

Recovery não supõe que um timeout de rede significa ausência de publicação.

## `startup_audit.py`

Executa a auditoria antes de abrir o fluxo normal:

- itens persistidos;
- estados;
- workspaces;
- órfãos;
- publicações;
- confirmações;
- cleanup.

O log de startup é a fotografia inicial da consistência.

## `production_contracts.py`

Define os contratos abstratos dos adapters de produção, incluindo a identidade da mensagem fonte.

---

# 7. Coordinator

Arquivo:

```
armored_core/coordinator.py
```

É a **única raiz de composição**.

Responsabilidades:

1. adquirir runtime lock;
2. executar startup audit/recovery;
3. iniciar CATCH-UP quando histórico ainda não foi concluído;
4. manter processamento sequencial;
5. fazer a transição para LIVE;
6. continuar vivo durante erros transitórios;
7. liberar recursos no shutdown.

## 7.1 Execução contínua

A vida útil real usa um único loop asyncio.

Isso é importante para Telethon porque o client não deve ser amarrado a um loop diferente a cada ciclo.

## 7.2 CATCH-UP → LIVE

No fluxo normal:

```
histórico incompleto
      ↓
CATCH-UP
      ↓
histórico concluído
      ↓
LIVE contínuo
```

O modo de certificação com limite de 3 itens existe apenas para testar rapidamente a transição. Não deve ser usado para o CATCH-UP histórico de produção.

## 7.3 Runtime lock

Existe um lock persistente para evitar duas instâncias concorrentes do Coordinator.

O lock também possui heartbeat.

---

# 8. ArmoredVision

Arquivos:

```
ArmoredVision/service.py
ArmoredVision/modules/v1/shopee_api.py
ArmoredVision/modules/v1/shopee_resolver.py
```

Responsabilidade: identidade comercial do produto.

## 8.1 `service.py`

Coordena:

- leitura da URL Shopee;
- resolução do link;
- consulta à API de afiliados;
- obtenção do nome do produto;
- obtenção/geração da affiliate URL.

A V1 usa resolução exata de `shop_id + item_id`.

## 8.2 `shopee_resolver.py`

Resolve URLs Shopee, incluindo redirecionamento/normalização necessário para obter a identidade do produto.

## 8.3 `shopee_api.py`

Encapsula as chamadas da API de afiliados.

## 8.4 WAITING_VISION

Quando o produto não pode ser resolvido pela V1, o item não deve ser falsamente aprovado nem enviado ao Studio.

Ele pode ficar:

```
WAITING_VISION
```

O original permanece preservado.

Isso prepara o caminho para uma futura Vision V2 sem alterar a pipeline.

---

# 9. ArmoredStudio

Arquivos de entrada:

```
ArmoredStudio/service.py
ArmoredStudio/unified.py
```

O projeto usa **um único Studio canônico**.

O Studio executa análise obrigatória seguida de processamento.

## 9.1 `unified.py`

É o motor unificado de processamento.

Responsabilidade:

- receber o `Item`;
- consumir o original/working adequado;
- executar análise;
- executar processamento;
- produzir o resultado final.

## 9.2 `service.py`

É a fronteira usada pelo Coordinator/Pipeline.

Valida a presença de FFmpeg quando a execução real exige isso e retorna `StudioResult`.

## 9.3 `analysis/`

Módulos presentes:

- `video.py` — metadados/estrutura de vídeo.
- `blackbar.py` — análise de barras pretas.
- `banner.py` — regras ligadas a banners.
- `banner_analyzer.py` — análise de banners.
- `veo_detector.py` — detector relacionado ao conteúdo/saída VEO.
- `gemini_detector.py` — detector de marca/artefato Gemini.
- `export_planner.py` — montagem do plano de exportação.
- `export_plan_validator.py` — validação do plano.
- `export_executor.py` — execução da etapa de exportação.
- `logger.py` — logging especializado da análise.

A pasta `analysis` é análise/plano, não outra pipeline.

## 9.4 `processing/`

Módulos presentes:

- `rvc.py` — wrapper do RVC;
- `tool_paths.py` — resolução dos binários/ferramentas;
- `finalizer.py` — finalização/consolidação da saída.

## 9.5 RVC

O RVC é parte do Studio.

Runtime local:

```
ArmoredStudio/runtime/rvc/env
```

O código permite resolver runtime externo através de configuração apropriada.

GPU é opcional para a arquitetura; o ambiente testado também possui fallback CPU.

## 9.6 Assets

```
ArmoredStudio/assets/
```

Contém os assets necessários ao processamento, como banner e áudio.

---

# 10. ArmoredHub

Arquivos:

```
ArmoredHub/service.py
```

O Hub é a fronteira de publicação.

Responsabilidades:

- validar resultado;
- persistir intenção/registro de publicação;
- publicar no Telegram;
- registrar o `message_id` real;
- confirmar publicação;
- tratar janela ambígua de timeout;
- impedir republicação insegura.

## 10.1 Destino

Configuração:

```
ARMORED_CREATOR_GROUP_ID
ARMORED_HUB_TOPIC_ID=228
```

O topic 228 é o destino validado.

## 10.2 Dry-run

```
ARMORED_HUB_DRY_RUN=1
```

bloqueia publicação real.

Para operação/certificação real:

```
ARMORED_HUB_DRY_RUN=0
```

## 10.3 Idempotência

Antes de uma nova publicação, o Hub considera o registro persistido.

Resultado:

- `CONFIRMED` → não publica novamente;
- `ABSENT` → publicação ainda pode ser necessária;
- `UNKNOWN` → não repete cegamente.

## 10.4 Janela crítica pós-envio

Após o Bot API retornar o `message_id`, esse ID é persistido antes de qualquer operação que possa falhar.

Isso cobre:

```
Telegram aceitou
↓
processo caiu antes do restante
↓
restart
↓
Recovery encontra o message_id
↓
verifica Telegram
↓
não duplica
```

---

# 11. Confirmação de publicação

A publicação real não depende somente de “send_video terminou”.

O Hub verifica a mensagem no destino.

A correspondência considera:

- chat de destino;
- topic;
- caption/affiliate URL;
- presença de mídia de vídeo/documento.

Resultado:

```
CONFIRMED
ABSENT
UNKNOWN
```

### Regra de segurança

> **UNKNOWN nunca vira republicação automática.**

Esse princípio protege contra duplicatas quando a rede falha depois que o Telegram já recebeu a mídia.

---

# 12. Storage canônico

Estrutura esperada:

```
storage/
├── database/
│   └── armoredcreator.db
├── videos/
│   └── {item_id}/
│       ├── original.*
│       ├── working.*
│       └── result.*
├── logs/
└── backups/
```

O nome exato dos arquivos derivados pode variar, mas o workspace pertence exclusivamente ao item.

## Não recriar filas físicas

Não utilizar como arquitetura:

```
storage/sync/
storage/queue/
storage/publish_queue/
storage/pipeline/
storage/hub/
storage/generated/
storage/rejected/
storage/archive/
storage/input/
storage/output/
storage/temp/
```

O script de reset pode remover resíduos legados conhecidos; isso não significa que eles façam parte da arquitetura.

---

# 13. Checkpoints

Os checkpoints LIVE ficam persistidos por tópico no SQLite.

Princípio:

```
checkpoint
=
último ponto confirmado até onde o processamento foi realmente concluído
```

Materialização antecipada não pode criar um checkpoint falso.

Após restart, o processo não depende do checkpoint em RAM.

---

# 14. Recovery

Recovery é parte da arquitetura, não uma ferramenta opcional externa.

## Situações

### RECEIVED + original ausente

Pode significar download interrompido.

O `.part` deve ser tratado como incompleto e o candidato precisa ser redescoberto/materializado novamente.

### STUDIO + working presente

Pode continuar/reusar o artefato durável, conforme o contrato do Studio.

### STUDIO + working ausente + original presente

O original é a fonte para reconstrução.

### PUBLISHING + result presente

Não publicar cegamente. Primeiro verificar o estado externo.

### PUBLISHED

Não republicar.

### UNKNOWN

Parar a decisão automática e manter o item recuperável.

---

# 15. START_ALL.bat

Arquivo:

```
START_ALL.bat
```

É o launcher operacional único.

Fluxo:

```
START_ALL.bat
   ↓
run_coordinator.py
   ↓
Coordinator
   ↓
Sync + Vision + Studio + Hub
```

Não iniciar manualmente cinco pipelines para produção.

---

# 16. run_coordinator.py

Define as condições de entrada do processo:

- root;
- Telegram real;
- logging;
- Coordinator;
- poll interval;
- `ARMORED_MAX_CYCLES` quando explicitamente usado em laboratório.

Padrão real:

```
ARMORED_REAL_TELEGRAM=1
```

---

# 17. Configuração relevante

```
ARMORED_ROOT=.
ARMORED_REAL_TELEGRAM=1

ARMORED_HUB_DRY_RUN=0

ARMORED_SYNC_SOURCE=-1003788989075
ARMORED_SYNC_SOURCE_ID=-1003788989075

ARMORED_CREATOR_GROUP_ID=-1004341972306
ARMORED_HUB_TOPIC_ID=228

TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
ARMORED_CREATOR_BOT_TOKEN=...

SHOPEE_APP_ID=...
SHOPEE_SECRET_KEY=...
SHOPEE_AFFILIATE_API_URL=https://open-api.affiliate.shopee.com.br/graphql
```

### Certificação limitada

Somente para o teste rápido CATCH-UP → LIVE:

```
ARMORED_SYNC_CATCHUP_LIMIT=3
ARMORED_CERT_CATCHUP_THEN_LIVE=1
```

### Produção/CATCH-UP histórico completo

Remover essas duas variáveis:

```
Remove-Item Env:ARMORED_SYNC_CATCHUP_LIMIT -ErrorAction SilentlyContinue
Remove-Item Env:ARMORED_CERT_CATCHUP_THEN_LIVE -ErrorAction SilentlyContinue
```

Assim o CATCH-UP não fica artificialmente limitado.

### Watchdog de descoberta LIVE

```
ARMORED_LIVE_DISCOVERY_TIMEOUT=30
```

Esse valor limita apenas uma descoberta Telegram LIVE que ficou travada. Não limita download de vídeo nem processamento Studio.

---

# 18. Laboratório de reset

Arquivo:

```
scripts/reset_certification_lab.ps1
```

Uso:

```
.\scripts\reset_certification_lab.ps1
```

O script apaga apenas o estado de laboratório:

- SQLite;
- workspaces de vídeo;
- logs;
- diretórios legados conhecidos.

Preserva:

- credentials;
- sessão Telegram;
- assets;
- código.

Ele exige:

```
RESET ARMORED
```

### Observação Git

O reset pode remover `.gitkeep` de:

```
storage/logs/.gitkeep
storage/videos/.gitkeep
```

Se o Git ficar sujo somente por esses arquivos, restaurar:

```
git restore storage/logs/.gitkeep storage/videos/.gitkeep
```

Não commitar estado de laboratório.

---

# 19. Testes automatizados

O projeto usa o workflow:

```
.github/workflows/tests.yml
```

que executa:

```
python -m unittest discover -s tests -v
```

A suíte cobre áreas como:

- arquitetura;
- invariantes;
- SQLite;
- pipeline;
- recovery;
- startup audit;
- runtime lock;
- Sync lifecycle;
- CATCH-UP;
- restart;
- publication;
- Telegram verification;
- Telegram session reconnect;
- LIVE reconnect;
- LIVE polling rate;
- RVC wrapper;
- Vision/Studio contract;
- WAITING_VISION.

Também existem testes E2E sob:

```
tests/e2e/
```

e scripts específicos em:

```
scripts/
```

---

# 20. Testes reais já executados

## 20.1 Três itens reais

Foram processados no Telegram real:

```
1383
1174
823
```

Todos chegaram a publicação confirmada e cleanup.

Estado observado no restart:

```
1383 → PUBLISHED → CONFIRMED#870 → cleanup OK
1174 → PUBLISHED → CONFIRMED#872 → cleanup OK
823  → PUBLISHED → CONFIRMED#873 → cleanup OK
```

Auditoria:

```
itens=3
publicados=3
pendentes=0
falhos=0
limpos=3
workspaces=3
órfãos=0
publicações_confirmadas=3
ambíguas=0
```

## 20.2 CATCH-UP limitado → LIVE

O teste de certificação com limite 3 foi executado em Telegram real.

Sequência comprovada:

```
3 itens completos
→ cutover histórico seguro
→ LIVE
```

## 20.3 Restart em LIVE

Após reiniciar:

```
Modo SQLite: LIVE
startup audit consistente
3 itens PUBLISHED
0 pendentes
0 falhos
0 órfãos
→ LIVE
```

Nenhum dos três foi republicado.

## 20.4 Queda de Wi-Fi

O teste real mostrou:

```
queda de rede
→ erro de transporte
→ Coordinator continua vivo
→ sessão é liberada/reconstruída
→ novas tentativas
→ conexão restaurada
```

Foi observada a mensagem:

```
[SYNC][TELEGRAM] sessão Telegram conectada/reconectada
```

O antigo erro:

```
sqlite3.OperationalError: database is locked
```

não reapareceu após a correção de lifecycle do Telethon.

## 20.5 FloodWait

Durante um teste anterior, o Telegram retornou:

```
FloodWaitError
```

porque a implementação anterior varria os tópicos repetidamente.

Isso foi corrigido com polling LIVE round-robin de um tópico por ciclo.

O cenário de ausência de conteúdo novo deve ser considerado **idle operation**, não falha.

---

# 21. O que NÃO foi certificado ainda

### CATCH-UP histórico completo

A fonte conhecida possui aproximadamente **308 conteúdos monitorados**.

Ainda falta executar a coleta/processamento histórico completo em um laboratório limpo.

### Novo candidato durante LIVE

Não é possível inserir manualmente um vídeo no grupo fonte porque o grupo pertence a terceiros.

Por isso, a chegada de um conteúdo novo enquanto o processo está em LIVE não pode ser forçada manualmente neste ambiente.

### Perda abrupta de energia

Ainda não foi realizado teste físico de desligamento da máquina no meio do processamento.

Esse cenário é diferente de `CTRL+C`.

---

# 22. Certificação histórica — procedimento oficial

Este é o próximo teste.

## Etapa 1 — parar o processo atual

```
CTRL+C
```

## Etapa 2 — garantir o commit congelado

```powershell
cd C:\Users\Administrador\Downloads\ArmoredCreator

git fetch origin
git reset --hard origin/main
git log -1 --oneline
```

O commit exibido deve ser o **commit de congelamento documentado após este README**.

## Etapa 3 — retirar limites artificiais

```powershell
Remove-Item Env:ARMORED_SYNC_CATCHUP_LIMIT -ErrorAction SilentlyContinue
Remove-Item Env:ARMORED_CERT_CATCHUP_THEN_LIVE -ErrorAction SilentlyContinue

$env:ARMORED_REAL_TELEGRAM="1"
$env:ARMORED_HUB_DRY_RUN="0"
```

## Etapa 4 — reset limpo

```powershell
.\scripts\reset_certification_lab.ps1
```

Confirmar:

```
RESET ARMORED
```

Esse reset não apaga a sessão autenticada nem os assets.

## Etapa 5 — iniciar CATCH-UP completo

```powershell
$env:ARMORED_REAL_TELEGRAM="1"
$env:ARMORED_HUB_DRY_RUN="0"

.\START_ALL.bat
```

### Não definir

```
ARMORED_SYNC_CATCHUP_LIMIT
ARMORED_CERT_CATCHUP_THEN_LIVE
```

O CATCH-UP deverá ser ilimitado.

---

# 23. O que observar durante os ~308 conteúdos

A certificação histórica não é apenas “chegou ao LIVE”.

Precisamos observar:

### Descoberta

Cada candidato deve ter:

- Telegram ID válido;
- tópico correto;
- URL Shopee identificável.

### Materialização

O vídeo deve cair diretamente no workspace canônico.

Não devem aparecer lotes em:

```
storage/sync
storage/queue
storage/sync video
```

### Processamento

Para cada item:

```
Vision
→ Studio
→ RVC/FFmpeg quando aplicável
→ resultado
```

### Publicação

```
PUBLISHING
→ Telegram
→ message_id
→ confirmação
→ PUBLISHED
```

### Cleanup

Somente depois da confirmação.

### Sequência

Nunca:

```
item A processando
+
item B materializando
```

O esperado é:

```
A completo
↓
B começa
↓
B completo
↓
C começa
```

---

# 24. Critérios de sucesso do CATCH-UP histórico

O teste será considerado concluído quando:

- o histórico elegível for totalmente percorrido;
- nenhum item ficar bloqueado sem motivo conhecido;
- cada item concluído tiver identidade persistida;
- checkpoints estiverem persistidos;
- publicações confirmadas estiverem registradas;
- cleanup estiver correto;
- originais permanecerem preservados;
- não houver cópias em filas físicas antigas;
- não houver republicação duplicada;
- o último ciclo concluir o histórico;
- o Coordinator entrar automaticamente em LIVE.

O estado esperado no fim é conceitualmente:

```
CATCH-UP
   ↓
último candidato histórico concluído
   ↓
checkpoints persistidos
   ↓
historical_complete = true
   ↓
LIVE
```

---

# 25. Regra de falha durante CATCH-UP

Uma falha em um item não pode ser mascarada.

O item deve permanecer rastreável no SQLite.

O Coordinator deve preservar o máximo de informação possível:

```
content_id
telegram_message_id
state
error
workspace
original
attempts
recovery_count
```

Não avançar o checkpoint como se o item tivesse terminado quando isso não aconteceu.

---

# 26. Regra de crash/restart durante CATCH-UP

Em caso de:

- queda do processo;
- reinício;
- perda de rede;
- falha do Studio;
- falha do Hub;
- timeout ambíguo;

o próximo startup deve usar:

```
SQLite
+
workspace
+
publication
+
Telegram
```

para decidir o que fazer.

Não usar apenas:

```
"qual foi o último arquivo criado?"
```

---

# 27. Pós-CATCH-UP

Quando o CATCH-UP completo terminar, o processo deve permanecer rodando:

```
[COORDINATOR][LIVE] ... monitoramento contínuo iniciado
```

Nesse estágio não se espera atividade constante.

Pode haver:

```
horas
ou
dias
```

sem novo conteúdo.

Isso é operação normal.

Não devemos provocar atividade artificial só para produzir logs.

O sistema deve permanecer aguardando mensagens posteriores aos checkpoints.

---

# 28. Diferença entre certificação rápida e produção

## Certificação rápida

```
ARMORED_SYNC_CATCHUP_LIMIT=3
ARMORED_CERT_CATCHUP_THEN_LIVE=1
```

Objetivo: comprovar rapidamente a transição CATCH-UP → LIVE.

## CATCH-UP real

```
sem limite
```

Objetivo: processar o histórico inteiro.

## LIVE real

```
histórico concluído
+
checkpoints persistidos
+
polling contínuo
```

Objetivo: monitoramento permanente.

As variáveis de certificação não devem permanecer habilitadas por engano numa operação de histórico completo.

---

# 29. Regras que não devem ser reintroduzidas

Não voltar a:

- múltiplos Coordenators;
- múltiplos processos Studio concorrentes;
- fila física de vídeos;
- pasta de Sync separada;
- cópias intermediárias espalhadas;
- timeout total fixo para downloads;
- timeout de 120/180 segundos para considerar download morto;
- republicação por “garantia”;
- promoção de UNKNOWN para ABSENT;
- checkpoint antes da conclusão;
- scan completo dos 16 tópicos a cada poll curto;
- criação de novo event loop por ciclo de Telethon;
- login interativo no Hub;
- caminhos absolutos dependentes da máquina.

---

# 30. Segurança operacional das credenciais

Segredos devem ficar fora do código:

- Telegram API ID/hash;
- Bot token;
- Shopee App ID;
- Shopee Secret Key.

A sessão Telegram existente pode ser preservada pelo reset de certificação.

Nunca gravar tokens em README, testes ou commits.

---

# 31. Estrutura resumida do repositório

```
ArmoredCreator/
├── ArmoredSync/
│   └── service.py
├── ArmoredVision/
│   ├── service.py
│   └── modules/v1/
│       ├── shopee_api.py
│       └── shopee_resolver.py
├── ArmoredStudio/
│   ├── analysis/
│   │   ├── video.py
│   │   ├── blackbar.py
│   │   ├── banner.py
│   │   ├── banner_analyzer.py
│   │   ├── veo_detector.py
│   │   ├── gemini_detector.py
│   │   ├── export_planner.py
│   │   ├── export_plan_validator.py
│   │   ├── export_executor.py
│   │   └── logger.py
│   ├── processing/
│   │   ├── rvc.py
│   │   ├── tool_paths.py
│   │   └── finalizer.py
│   ├── assets/
│   ├── service.py
│   └── unified.py
├── ArmoredHub/
│   └── service.py
├── armored_core/
│   ├── coordinator.py
│   ├── database.py
│   ├── models.py
│   ├── pipeline.py
│   ├── recovery.py
│   ├── production_contracts.py
│   ├── services.py
│   ├── startup_audit.py
│   └── storage.py
├── scripts/
│   ├── reset_certification_lab.ps1
│   └── retest_telegram_video_metadata.py
├── tests/
│   ├── e2e/
│   └── test_*.py
├── START_ALL.bat
├── run_coordinator.py
├── requirements.txt
└── README.md
```

---

# 32. Mapa mental da operação

```
START_ALL
   │
   ▼
Coordinator
   │
   ├── Startup Audit
   │       │
   │       └── Recovery
   │
   ├── CATCH-UP
   │       │
   │       ├── Sync
   │       ├── Vision
   │       ├── Studio
   │       ├── Hub
   │       ├── Confirm
   │       └── Cleanup
   │
   └── LIVE
           │
           ├── 1 tópico/ciclo
           ├── 1 candidato
           ├── 1 item ativo
           └── volta ao início
```

---

# 33. Definição de “fechado”

A reconstrução arquitetural está em estado de certificação final quando:

- a composição canônica está estável;
- o armazenamento é único;
- o SQLite é a verdade interna;
- o Coordinator é a única raiz;
- CATCH-UP e LIVE usam a mesma pipeline;
- processamento é sequencial;
- publicação é idempotente;
- confirmação é independente;
- Recovery é determinístico;
- restart preserva estado;
- reconexão Telegram não gera lock;
- polling LIVE não agride o rate limit desnecessariamente.

A partir deste README, o trabalho passa de **reconstrução** para **certificação do histórico completo**.

---

# 34. Próximo marco: CATCH-UP histórico completo

O próximo teste oficial é:

```
RESET LIMPO
      ↓
CATCH-UP ILIMITADO
      ↓
~308 conteúdos elegíveis
      ↓
Vision
      ↓
Studio/RVC
      ↓
Hub
      ↓
Telegram destino
      ↓
confirmação
      ↓
cleanup
      ↓
checkpoints
      ↓
LIVE
```

Durante esse teste, o código deve ser tratado como congelado.

O relatório final deve registrar:

- commit exato usado;
- quantidade de itens descobertos;
- quantidade concluída;
- quantidade publicada;
- quantidade confirmada;
- quantidade com cleanup;
- falhas;
- itens em recovery/WAITING_VISION;
- checkpoints finais;
- existência/ausência de órfãos;
- diretórios legados encontrados;
- estado final do Coordinator.

---

## Status de certificação

| Área | Estado |
|---|---|
| Arquitetura canônica | ✅ implementada |
| SQLite central | ✅ |
| Storage único | ✅ |
| Pipeline sequencial | ✅ |
| ArmoredSync real | ✅ validado |
| ArmoredVision V1 | ✅ integrada |
| ArmoredStudio/RVC | ✅ validado |
| ArmoredHub Telegram | ✅ publicação real validada |
| Confirmação Telegram | ✅ |
| Recovery determinístico | ✅ |
| Restart LIVE | ✅ |
| Wi-Fi OFF/ON | ✅ |
| Reconexão Telethon | ✅ |
| FloodWait por polling excessivo | ✅ corrigido |
| CATCH-UP limitado → LIVE | ✅ comprovado |
| CATCH-UP histórico completo (~308) | ⏳ próximo teste |
| Novo vídeo forçado no LIVE | ⏳ indisponível com fonte de terceiros |
| Queda física de energia | ⏳ não testada |
| Certificação final do projeto | ⏳ após CATCH-UP histórico |

---

# 35. Regra final

**Não alterar a arquitetura durante o CATCH-UP histórico.**

Primeiro medir.

Depois, somente se o teste encontrar um defeito real e reproduzível, abrir uma correção isolada com novo commit e nova certificação.

O objetivo agora é simples:

```
congelar
→ resetar laboratório
→ coletar o histórico inteiro
→ processar sequencialmente
→ confirmar tudo
→ entrar em LIVE
→ registrar o resultado
```
