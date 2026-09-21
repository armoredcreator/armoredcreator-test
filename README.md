# ArmoredCreator — Reconstrução, Arquitetura e Certificação

Laboratório isolado da reconstrução do **ArmoredCreator**. Este documento é o registro técnico da arquitetura canônica, dos contratos entre módulos, das decisões de reconstrução, dos testes executados e do que ainda precisa ser certificado operacionalmente.

> **Regra de segurança:** este repositório é o laboratório. O repositório oficial `armoredcreator/armoredcreator` e a branch `audit/baseline-2026-09-19` permanecem intocáveis.

**Branch de trabalho:** `refactor/closure-batch`  
**Base:** `refactor/single-storage-pipeline`  
**HEAD atual da branch:** `cd4c9f90f4c61d74dfe4c13bb0027f77074ee74a`

---

## 1. Objetivo da reconstrução

O ArmoredCreator não é tratado como um aplicativo monolítico. O comportamento original é composto por serviços/processos independentes coordenados por uma única composição operacional.

A reconstrução tem como objetivo preservar o comportamento funcional confirmado no backup, eliminando a arquitetura física antiga e consolidando:

- armazenamento único;
- SQLite como fonte de verdade;
- processamento estritamente sequencial;
- identidade determinística por mensagem Telegram;
- recuperação após crash/restart;
- publicação idempotente;
- confirmação externa antes de cleanup;
- execução portátil, sem caminhos absolutos da máquina;
- um único launcher operacional;
- CATCH-UP e LIVE usando a mesma pipeline.

Princípio central:

**SQLite = verdade. Filesystem = projeção/runtime. Telegram = realidade externa. Coordinator = composição.**

---

# 2. Arquitetura definitiva

Fluxo canônico:

```
Telegram fonte
    ↓
ArmoredSync
    ↓
SQLite + storage/videos/{telegram_message_id}/
    ↓
Coordinator
    ↓
Vision
    ↓
Studio
    ↓
Hub
    ↓
Telegram destino
    ↓
confirmação determinística
    ↓
PUBLISHED
    ↓
cleanup
    ↓
próximo item
```

Existe exatamente **um item ativo no processamento**.

A descoberta de vários itens pelo Sync não cria processamento paralelo:

```
1383 → Vision → Studio → Hub → cleanup
                                  ↓
1384 → Vision → Studio → Hub → cleanup
                                  ↓
1385 → ...
```

Não fazem parte da arquitetura final:

- RabbitMQ;
- Redis;
- Celery;
- Kafka;
- filas físicas de vídeo;
- diretórios de queue;
- múltiplos Studios concorrentes;
- pipeline separado para LIVE;
- múltiplos launchers operacionais;
- cópias espalhadas do mesmo vídeo.

---

# 3. Componentes

## 3.1 ArmoredSync

Responsabilidade: **descobrir e ingerir conteúdo da fonte**.

O Sync não executa Vision, Studio ou Hub.

Possui dois modos:

```
CATCH-UP
   ↓
histórico
   ↓
LIVE
   ↓
novas mensagens
```

A identidade é sempre o `telegram_message_id`.

### Regra de candidato

Um conteúdo é candidato quando:

1. existe vídeo + link Shopee na mesma mensagem; ou
2. existe vídeo e a mensagem imediatamente seguinte possui o link Shopee, desde que essa mensagem seguinte não seja vídeo.

Não é permitido procurar links arbitrariamente distantes.

### CATCH-UP

O histórico percorre os tópicos configurados, mas **nunca materializa um lote**.

Para cada candidato:

```
descobrir 1 candidato
→ materializar somente esse candidato
→ fechar/liberar a sessão Sync
→ Vision
→ Studio
→ Hub
→ confirmação Telegram
→ cleanup
→ próximo candidato
```

O próximo vídeo só pode ser baixado depois que o item anterior deixou a etapa Sync e entrou no processamento sequencial. Uma falha de materialização não cria uma fila de downloads: o item permanece persistido/recuperável e o Coordinator pode seguir para o próximo candidato.

O checkpoint só avança depois que o ORIGINAL do candidato foi materializado de forma durável. O estado de processamento continua no SQLite e permite recovery após restart.

### LIVE

LIVE usa exatamente o mesmo contrato de item único:

```
descobrir 1 candidato
→ materializar somente esse candidato
→ liberar Sync
→ processar
→ confirmar
→ cleanup
→ próxima descoberta
```

Não existe batch de materialização no LIVE. A implementação pode limitar a descoberta a um candidato por chamada, mas nunca materializa vários vídeos antes do processamento.

Os checkpoints ficam em SQLite na estrutura `sync_topics`. Uma pequena sobreposição continua permitida para detectar:

```
vídeo sem link
↓
mensagem seguinte com Shopee
```

A sobreposição é deduplicada pelo ID Telegram.

---

# 4. SQLite

O banco é a fonte de verdade do pipeline.

Local canônico:

```
storage/database/armoredcreator.db
```

## 4.1 Items

A entidade principal contém, entre outros:

- `content_id`;
- `telegram_message_id`;
- `source_id`;
- `topic_id`;
- `topic_name`;
- `original_url`;
- `state`;
- `original_path`;
- `original_sha256`;
- `working_path`;
- `result_path`;
- `affiliate_name`;
- `affiliate_url`;
- `attempts`;
- `recovery_count`;
- `cleanup_completed`;
- `last_error`;
- timestamps.

`telegram_message_id` possui identidade única.

## 4.2 state_events

Registra transições:

```
id
content_id
old_state
new_state
reason
created_at
```

Isso mantém histórico determinístico das mudanças.

## 4.3 publications

Registra a publicação externa e sua confirmação:

- `content_id`;
- `idempotency_key`;
- `published_message_id`;
- `confirmed`;
- `verification_status`;
- `destination_chat_id`;
- `destination_topic_id`;
- `verified_at`;
- timestamps.

A publicação não é considerada definitivamente concluída apenas porque uma chamada de envio retornou.

## 4.4 sync_topics

Mantém os checkpoints persistentes do monitoramento Telegram.

Um restart não depende do estado da memória do processo.

## 4.5 WAL e fechamento

O SQLite permanece em WAL.

O fechamento foi tornado seguro para cenários em que ainda exista outra conexão SQLite durante shutdown. O checkpoint WAL pode ser adiado quando outra conexão estiver aberta; a conexão atual ainda é fechada corretamente.

---

# 5. Coordinator

O Coordinator é a **única raiz de composição operacional**.

Responsabilidades:

1. recuperar itens pendentes no startup;
2. executar CATCH-UP quando necessário;
3. processar exatamente um item por vez;
4. entrar no LIVE;
5. alimentar os mesmos métodos da pipeline;
6. manter heartbeat/runtime lock;
7. liberar o lock em shutdown normal ou erro;
8. manter o processo vivo durante o monitoramento contínuo.

Não existe uma pipeline paralela para LIVE.

## 5.1 Construção

`Coordinator.build(root)` resolve a composição:

- root portátil;
- arquivos `.env`;
- SQLite;
- Storage;
- fonte Telegram real ou fonte local de laboratório;
- Vision;
- Studio;
- Publisher;
- Recovery.

O processo real usa:

```
ARMORED_REAL_TELEGRAM=1
```

## 5.2 Startup

A ordem é:

```
runtime lock
↓
startup recovery
↓
CATCH-UP, se necessário
↓
LIVE contínuo
```

Itens recuperáveis são reconciliados antes da coleta/processamento normal.

## 5.3 Estados

Fluxo normal:

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

Também existem:

- `WAITING_VISION`;
- `FAILED`;
- `RECOVERY`;

`FAILED` não é automaticamente reprocessado em loop. Recovery manual/determinístico pode reconciliar um item explicitamente.

---

# 6. Pipeline

A pipeline é sequencial.

## RECEIVED

Item ingerido e original persistido.

## VISION

Resolve produto, URL Shopee e dados de afiliado.

## STUDIO

Executa o processamento audiovisual.

## PUBLISHING

Existe resultado durável e a publicação está sendo reconciliada/enviada.

## PUBLISHED

Telegram confirmou a publicação.

## CLEANUP

Remove somente artefatos temporários/derivados permitidos.

## DONE

Pipeline encerrada para o item.

## FAILED

Falha persistida. Não deve provocar loop automático infinito.

## RECOVERY

Estado temporário de reconciliação após restart/falha.

---

# 7. ArmoredVision

Vision recebe um item já ingerido pelo Sync.

Responsabilidades:

- resolver a URL Shopee;
- obter dados do produto;
- obter/generar affiliate URL;
- determinar `affiliate_name`;
- persistir os dados no SQLite.

Vision não cria fila física.

A pasta `ArmoredVision/modules/v1` relacionada à integração Shopee não representa uma segunda arquitetura de Studio. A unificação V1/V2 refere-se especificamente ao ArmoredStudio.

---

# 8. ArmoredStudio

Existe **um único ArmoredStudio**.

Arquitetura:

```
ArmoredStudio/
├── analysis/
├── processing/
│   ├── rvc.py
│   ├── tool_paths.py
│   └── finalizer.py
├── runtime/
│   └── rvc/
└── unified.py
```

Não existem mais módulos físicos `Studio/v1` e `Studio/v2` como arquiteturas concorrentes.

## 8.1 Analysis

Inclui os analisadores e planejadores utilizados pelo processamento, como:

- análise de vídeo;
- black bars;
- banners;
- detectores;
- planejamento de exportação;
- validação;
- execução;
- logging.

## 8.2 Processing

Contém:

- RVC;
- resolução de ferramentas;
- finalização.

## 8.3 RVC

RVC é funcionalidade interna do Studio.

Código:

```
ArmoredStudio/processing/rvc.py
```

Runtime pesado é local e não deve ser versionado como código do projeto.

A ausência de GPU Nvidia suportada não altera a arquitetura: o processamento pode utilizar fallback CPU.

## 8.4 Working e result

O Studio trabalha dentro do workspace canônico do item.

O Coordinator/Recovery não depende de nomes arbitrários espalhados pelo projeto.

---

# 9. ArmoredHub

Hub é responsável pela publicação no destino.

Destino operacional validado:

```
Telegram destination
topic = 228
```

A publicação é idempotente.

A existência de uma tentativa anterior não significa que uma nova mensagem deve ser enviada automaticamente.

Antes de republicar, Recovery verifica a realidade externa.

---

# 10. Telegram

Existem duas funções distintas.

## Fonte

Telegram fornece:

- mensagens;
- vídeos;
- links;
- tópicos;
- IDs;
- checkpoints.

## Destino

Telegram recebe o resultado processado.

A publicação definitiva exige confirmação.

---

# 11. Confirmação determinística

A confirmação real usa MTProto.

A verificação utiliza:

- chat de destino;
- tópico de destino;
- identificador/sufixo da URL Shopee;
- caption esperada.

O resultado é classificado como:

```
CONFIRMED
ABSENT
UNKNOWN
```

## CONFIRMED

Existe mensagem correspondente no chat/tópico correto com caption correspondente.

O item pode avançar para `PUBLISHED`.

## ABSENT

Nenhuma publicação correspondente foi encontrada.

Uma publicação pode continuar/recomeçar de forma idempotente conforme o estado persistido.

## UNKNOWN

A realidade externa não pôde ser determinada com segurança.

**Não republicar automaticamente.**

Essa regra é fundamental para evitar duplicatas em cenários de timeout/crash.

---

# 12. Recovery determinístico

Recovery reconcilia três fontes:

```
SQLite
+
Filesystem
+
Telegram
```

A prioridade é sempre preservar a identidade e evitar duplicação.

## 12.1 STUDIO + working existente

Se o working existe e o item possui dados de Vision:

```
STUDIO
→ continuar Studio
```

Vision não precisa ser repetido.

## 12.2 STUDIO + working ausente

Se o original imutável existe:

```
original
→ reconstruir working
→ Studio
```

## 12.3 PUBLISHING + result existente

O resultado durável existe.

Antes de enviar novamente:

```
result
→ verificar Telegram
```

Se confirmado:

```
PUBLISHED
→ cleanup
```

## 12.4 Publicação já confirmada no DB

Se SQLite já possui confirmação:

```
PUBLISHED
→ cleanup
```

Sem nova publicação.

## 12.5 PUBLISHED

Item publicado nunca deve ser republicado.

## 12.6 UNKNOWN

Recovery para.

Não tenta “por garantia” publicar novamente.

---

# 13. Storage canônico

Estrutura definitiva:

```
storage/
├── database/
│   └── armoredcreator.db
├── videos/
│   └── {telegram_message_id}/
├── logs/
└── backups/
```

Cada item possui exatamente um workspace:

```
storage/videos/{telegram_message_id}/
```

Exemplo:

```
storage/videos/1383/1383_8KolJcZrfU.mp4
```

## Original

- permanente;
- imutável;
- nunca sobrescrito;
- referência de recuperação.

## Working

Artefato intermediário.

## Result

Resultado final pronto para publicação.

## Cleanup

Só ocorre depois da confirmação de publicação.

O original permanece.

### Diretórios proibidos na arquitetura final

Não recriar:

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

---

# 14. Portabilidade

Não existem caminhos fixos como:

```
C:\Users\Administrador\...
```

A raiz é resolvida a partir do projeto:

```
ARMORED_ROOT
```

O launcher utiliza `%~dp0`, permitindo mover o projeto para outra máquina/local.

---

# 15. START_ALL.bat

Existe um único launcher operacional:

```
START_ALL.bat
```

Ele:

1. entra na raiz do projeto;
2. resolve Python;
3. define `ARMORED_ROOT`;
4. define `PYTHONPATH`;
5. executa `run_coordinator.py`;
6. devolve o código de saída do Coordinator.

O launcher não inicia cinco pipelines independentes.

A composição é:

```
START_ALL
    ↓
Coordinator
    ↓
Sync + Vision + Studio + Hub
```

Launchers obsoletos foram removidos.

---

# 16. Process restart lab

Existe um laboratório específico para provar comportamento entre processos reais.

O teste executa o Coordinator em processos Python separados utilizando o mesmo SQLite.

Primeira execução:

```
STATE=PUBLISHED
CHECKPOINT=100
PUBLICATIONS=['lab-live-1']
```

Segunda execução, contra o mesmo estado:

```
STATE=PUBLISHED
CHECKPOINT=100
PUBLICATIONS=['lab-live-1']
```

Isso comprova no laboratório:

- persistência entre processos;
- persistência de checkpoint;
- deduplicação;
- publicação idempotente;
- reconhecimento de publicação existente;
- ausência de republicação após restart.

---

# 17. Testes automatizados

O número de testes deve ser considerado válido somente quando reproduzido no HEAD atual. O README não congela mais um contador histórico como prova de fechamento.

A certificação deve registrar a saída real de `python -m pytest -q` do commit testado.

Os testes cobrem, entre outros:

- SQLite;
- migração de schema;
- publicação;
- idempotência;
- recovery;
- Studio;
- lifecycle de Sync;
- CATCH-UP;
- LIVE;
- reconnect;
- Coordinator contínuo;
- runtime lock;
- process restart;
- contratos da arquitetura;
- launcher único.

O CI do HEAD documentado também foi aprovado.

---

# 18. Correções arquiteturais importantes

Principais correções consolidadas durante a reconstrução:

- migração correta do schema SQLite;
- migração de publication schema legado;
- fechamento seguro de SQLite com conexões concorrentes;
- Coordinator contínuo com runtime lock;
- liberação do lock após falha operacional;
- detecção correta da conexão Telethon através do client;
- CATCH-UP em lote;
- lifecycle correto da sessão Telegram durante CATCH-UP;
- release explícito da conexão direta de batch;
- LIVE em lote antes do processamento;
- persistência de checkpoints;
- process restart lab;
- remoção de launcher duplicado;
- remoção de demo entrypoint obsoleto;
- enforcement de launcher operacional único.

---

# 19. O que foi removido

A reconstrução removeu a dependência operacional de:

- filas físicas;
- diretórios de pipeline antigos;
- cópias intermediárias espalhadas;
- launcher duplicado;
- demo entrypoint;
- arquitetura antiga de múltiplos Studios;
- referências a caminhos fixos da máquina;
- componentes antigos que não pertencem à composição canônica.

---

# 20. O que foi preservado

Foi preservado o comportamento funcional considerado necessário:

- coleta Telegram;
- descoberta por tópicos;
- regra de associação vídeo/Shopee;
- identidade por Telegram ID;
- processamento sequencial;
- publicação Telegram;
- topic de destino;
- confirmação externa;
- original permanente;
- recovery;
- deduplicação;
- idempotência.

---

# 21. O que foi recriado

Foi recriada a arquitetura física de execução:

- Storage único;
- SQLite central;
- Coordinator;
- Pipeline sequencial;
- Recovery determinístico;
- lifecycle CATCH-UP/LIVE;
- publicação persistente;
- confirmação independente;
- launcher único;
- laboratório de restart.

---

# 22. O que foi implementado

Implementado e coberto por testes:

- estados;
- transições;
- eventos;
- publication records;
- checkpoints;
- recovery;
- storage canônico;
- idempotência;
- runtime lock;
- CATCH-UP sequencial;
- LIVE sequencial;
- restart lab;
- contratos de processamento.

---

# 23. Prova real já realizada — item 557

O item real `557` foi usado para validar recovery + publicação real.

Resultado documentado:

```
state: PUBLISHED
cleanup_completed: True
recovery_count: 4
attempts: 6

publication_message_id: 772
confirmed: 1
verification_status: CONFIRMED

destination_chat_id: -1004341972306
destination_topic_id: 228
```

A mensagem foi efetivamente recebida no Telegram.

Também foi demonstrado:

- recovery de item FAILED;
- reutilização de resultado durável;
- ausência de rerun desnecessário de Vision/Studio;
- publicação real;
- persistência do message ID;
- confirmação independente via MTProto;
- confirmação do tópico 228;
- cleanup após confirmação;
- preservação do original;
- ausência de duplicação.

Esse é um **marco real de integração**, mas não substitui a certificação do fluxo contínuo completo desde a fonte.

---

# 24. Estado atual do laboratório

O banco atual ainda contém evidências dos testes anteriores.

Existem itens históricos e estados como:

- PUBLISHED;
- FAILED;
- RECEIVED.

Existem também workspaces de testes anteriores.

Isso é proposital neste momento.

**Não apagar o banco ou storage antes de concluir a certificação operacional.**

Há também artefatos de migração/backup no ambiente local que serão tratados somente no reset final.

---

# 25. O que ainda NÃO está certificado

A suíte automatizada estar verde não significa que toda a operação real está certificada.

Ainda falta provar no ambiente Windows/Telegram real:

### 25.1 Coordinator contínuo real

O processo precisa permanecer vivo efetivamente em:

```
startup
→ recovery
→ CATCH-UP
→ LIVE
→ polling contínuo
```

### 25.2 Restart real durante LIVE

É necessário:

1. iniciar o Coordinator;
2. entrar em LIVE;
3. receber/processar estado;
4. encerrar/reiniciar o processo;
5. confirmar persistência;
6. confirmar que não duplica;
7. confirmar que o checkpoint continua correto.

### 25.3 START_ALL operacional

O launcher precisa iniciar a composição real sem intervenção manual adicional.

### 25.4 E2E real completo

A certificação definitiva deve demonstrar:

```
Telegram fonte
→ Sync
→ SQLite
→ Vision
→ Studio/RVC
→ Hub
→ Telegram destino
→ confirmação
→ cleanup
```

### 25.5 Segundo item sequencial

Após o primeiro item:

```
item A
→ publicação
→ confirmação
→ cleanup
→ item B
```

O segundo item precisa realmente entrar na mesma pipeline sem concorrência e sem travamento.

---

# 26. Critério de encerramento definitivo

O projeto pode ser considerado operacionalmente fechado quando todos os seguintes forem verdadeiros:

- [x] suíte automatizada verde;
- [x] SQLite central;
- [x] storage único;
- [x] pipeline sequencial;
- [x] recovery determinístico;
- [x] publicação idempotente;
- [x] confirmação Telegram;
- [x] process restart lab;
- [x] publicação real validada no item 557;
- [x] launcher único;
- [x] remoção das estruturas obsoletas;
- [ ] Coordinator contínuo real;
- [ ] restart real em LIVE;
- [ ] START_ALL real;
- [ ] E2E fonte → destino completo;
- [ ] segundo item sequencial real;
- [ ] reset final;
- [ ] CATCH-UP limpo;
- [ ] LIVE limpo;
- [ ] certificação final.

---

# 27. Plano final de fechamento

A partir daqui **não é necessário reconstruir novamente a arquitetura**.

A sequência correta é operacional:

## Fase A — certificação do processo

Executar:

```
START_ALL.bat
```

Verificar:

```
Coordinator iniciou
runtime lock
startup recovery
CATCH-UP
LIVE
processo permanece vivo
```

## Fase B — LIVE real

Com o Coordinator vivo:

- inserir/receber um novo candidato real;
- observar ingestão;
- observar processamento;
- observar publicação;
- confirmar Telegram;
- confirmar cleanup.

## Fase C — restart

Com LIVE ativo:

- interromper o processo;
- iniciar novamente;
- confirmar checkpoint;
- confirmar ausência de duplicação;
- confirmar continuidade.

## Fase D — segundo item

Enviar/obter dois candidatos distintos e confirmar:

```
A → completo
B → completo
```

sem processamento concorrente.

## Fase E — CATCH-UP limpo

Depois das provas, resetar o laboratório e executar um CATCH-UP controlado.

Confirmar:

- candidatos corretos;
- originals no workspace correto;
- nenhum diretório legado;
- nenhum download duplicado;
- processamento 1 por vez.

## Fase F — LIVE limpo

Com banco limpo e CATCH-UP concluído:

- entrar em LIVE;
- receber novo item;
- processar;
- publicar;
- confirmar;
- cleanup;
- permanecer aguardando novos itens.

## Fase G — certificação

Executar novamente:

```
python -m pytest -q
```

e registrar:

- commit;
- branch;
- resultado dos testes;
- prova E2E;
- prova restart;
- prova sequencial;
- estado final do storage;
- estado final do SQLite.

Somente então executar o **reset final definitivo** dos artefatos de laboratório.

---

# 28. Operação normal

Para iniciar:

```
START_ALL.bat
```

Para shutdown controlado:

```
CTRL+C
```

O processo operacional deve ser o Coordinator.

Não executar launchers antigos.

Não iniciar Vision, Studio ou Hub separadamente para operação normal.

---

# 29. Regra de ouro da reconstrução

O ArmoredCreator final não deve depender de memória do processo, nomes físicos arbitrários ou suposições sobre o que aconteceu antes do crash.

A pergunta para cada item deve poder ser respondida deterministically:

```
Quem é?
→ telegram_message_id

Qual é o estado?
→ SQLite

Qual é o original?
→ original_path

Existe working?
→ filesystem + DB

Existe resultado?
→ filesystem + DB

Foi publicado?
→ SQLite + Telegram

Foi confirmado?
→ publication + verificação externa

Pode limpar?
→ somente após confirmação

Pode republicar?
→ somente se a reconciliação determinar que é seguro
```

Essa é a base da recuperação e da operação contínua.

---

# 30. Estado de fechamento

**Arquitetura reconstruída:** SIM  
**Testes automatizados:** executar no HEAD atual antes de declarar fechamento  
**CI:** aprovado  
**Recovery determinístico:** implementado e testado  
**Publicação real:** comprovada  
**Confirmação Telegram real:** comprovada  
**Process restart lab:** comprovado  
**Storage único:** implementado  
**Coordinator contínuo:** implementado em código, certificação real pendente  
**E2E contínuo real:** certificação pendente  
**Processamento sequencial 1-item:** regra operacional corrigida nesta branch  
**Materialização em lote:** proibida pela arquitetura definitiva  
**Reset final:** pendente

O próximo trabalho não é mais reconstrução arquitetural.

É **certificação operacional real e fechamento do laboratório**.

---

# 31. Estratégia futura — ArmoredVision V2

A V2 será uma evolução interna da resolução de identidade, não uma segunda pipeline.

Fluxo futuro:

WAITING_VISION → busca de candidatos Shopee → reconciliação → revalidação → STUDIO

Se nenhum candidato for seguro, o item continua em WAITING_VISION.

## 31.1 Problema que a V2 resolve

A V1 consulta o par exato shop_id + item_id. Se a Shopee deixar de retornar essa oferta, isso significa somente que a V1 não conseguiu resolver a identidade naquele momento. Não devemos interpretar isso como prova de que o produto deixou de existir para sempre.

Possíveis causas incluem indisponibilidade da oferta, produto fora do catálogo de afiliados, relistagem, mudança de identidade ou indisponibilidade transitória da API.

Não assumir que uma futura relistagem reutilizará o mesmo item_id. A V2 precisa conseguir encontrar uma nova identidade candidata.

## 31.2 Capacidade da API Shopee

A investigação realizada encontrou suporte documentado para productOfferV2 com busca por keyword e filtros como shopId, itemId e categoria, além de campos como productName, imageUrl, productLink, offerLink, priceMin, priceMax, sales, ratingStar, commissionRate, shopId e shopName. A fonte consultada é uma documentação pública não oficial baseada na documentação da Shopee; portanto, os contratos finais deverão ser validados contra a documentação/Explorer oficial quando a V2 for implementada. citeturn0search0turn0search1

Isso torna a V2 tecnicamente viável para descoberta de candidatos, mas a API não prova sozinha que um candidato é o mesmo produto do vídeo. A reconciliação de identidade pertence à ArmoredVision.

## 31.3 Regra fundamental: não aceitar o primeiro resultado

A V2 nunca deve fazer keyword → primeiro resultado → publicação. Ela deve gerar candidatos, comparar evidências, eliminar incompatibilidades, revalidar o candidato escolhido por shop_id + item_id e somente então retornar VisionResult.

## 31.4 Evidências de identidade a preservar

Quando disponíveis, devem ser preservados: URL Shopee original; shop_id; item_id; nome; productLink; imagem; categorias; nome da loja; preço observado; sinais de oferta/comissão; timestamp da tentativa; versão da Vision; erro de resolução; e histórico dos candidatos avaliados.

A identidade antiga é evidência histórica. Uma eventual identidade nova é a identidade resolvida pela reconciliação.

## 31.5 Sinais de correspondência

- Nome: normalização, tokens e similaridade textual.
- Loja: shop_id e shopName são sinais fortes quando disponíveis; mudança de loja exige análise, não aceitação automática.
- Categoria: compatibilidade aumenta a evidência.
- Preço: somente evidência auxiliar, nunca identidade.
- Imagem: comparação visual entre a imagem original e a imagem do candidato.
- URL/IDs: quando a identidade exata ainda existe, shop_id + item_id continua sendo a confirmação mais forte.

O projeto já possui capacidade CLIP em ArmoredVision. Ela poderá ser usada como sinal visual da V2, mas CLIP sozinho nunca deve autorizar publicação.

## 31.6 Decisão da V2

A V2 deve produzir uma decisão explícita: RESOLVED, UNRESOLVED ou AMBIGUOUS.

RESOLVED: registrar identidade antiga e nova, revalidar a nova identidade, obter/generar affiliate URL e seguir para STUDIO.
UNRESOLVED: permanecer em WAITING_VISION.
AMBIGUOUS: permanecer em WAITING_VISION e não escolher arbitrariamente.

## 31.7 Versionamento da resolução

A resolução deve distinguir estratégias como VISION_V1_EXACT e VISION_V2_CANDIDATE. Isso permite auditar qual estratégia resolveu o produto, qual identidade foi usada, quando ocorreu e por quê.

## 31.8 A V2 não altera a pipeline

A V2 entra no mesmo contrato: WAITING_VISION → Vision V2 → STUDIO → PUBLISHING → PUBLISHED → cleanup.

Não criar queue de Vision, processo paralelo, novo banco, novo storage ou nova pipeline. V2 é evolução interna do estágio Vision.

## 31.9 Retry futuro

Não implementar retry infinito. Erros transitórios podem ter retry limitado com backoff. Produto não resolvido deve permanecer em WAITING_VISION. Candidato ambíguo também permanece em WAITING_VISION.

## 31.10 Scheduler futuro

Quando V2 estiver ativa, o Coordinator poderá consultar WAITING_VISION de forma controlada, respeitando intervalo mínimo, limite de tentativas, backoff, rate limit, prioridade e versionamento. Um item preso em Vision nunca pode bloquear os demais.

## 31.11 Persistência futura recomendada

Recomenda-se uma tabela própria vision_resolutions, separada de items, contendo pelo menos: content_id, vision_version, identidade original, identidade candidata, nomes, imagens, categorias, score, decisão, motivo e timestamp.

Isso permite auditar cada tentativa sem transformar items em tabela de histórico.

## 31.12 Testes obrigatórios da V2

1. V1 resolve normalmente.
2. V1 retorna zero produtos → WAITING_VISION.
3. WAITING_VISION sobrevive a restart.
4. Coordinator continua com outros itens.
5. V2 encontra candidato único.
6. V2 rejeita nome incompatível.
7. V2 rejeita categoria incompatível.
8. V2 considera loja.
9. V2 usa preço apenas como evidência.
10. V2 usa similaridade de imagem.
11. V2 não aceita CLIP sozinho.
12. V2 rejeita empate/ambiguidade.
13. V2 revalida shop_id + item_id.
14. V2 gera affiliate URL válida.
15. V2 não cria duplicata.
16. V2 mantém o original.
17. V2 registra identidade antiga e nova.
18. V2 respeita rate limit/backoff.
19. V2 não cria pipeline paralela.
20. V2 continua compatível com Recovery.

## 31.13 Critério de segurança

A V2 deve errar para o lado da não resolução, nunca para o lado de associar silenciosamente um produto diferente.

Regra: dúvida → WAITING_VISION.

---

# 32. Conclusão da investigação da API

Há base técnica suficiente para desenvolver a Vision V2 porque productOfferV2 oferece descoberta por palavra-chave e dados de produto que permitem construir uma camada própria de reconciliação. Isso não significa que a API forneça prova automática de identidade; essa responsabilidade continua no ArmoredVision. A documentação consultada deve ser tratada como referência de viabilidade, e os campos/limites finais devem ser confirmados contra a fonte oficial no início da implementação. citeturn0search0turn0search1

# 33. Situação da Vision após esta correção

A V1 continua sendo a estratégia ativa. Resolução exata bem-sucedida segue para STUDIO. Falha específica de produto não encontrado agora vai para WAITING_VISION, preserva o original, não chama Studio/Hub e não vira FAILED. Nenhuma V2 é ativada automaticamente neste momento.

# 34. Critérios adicionais de fechamento

- [x] produto não resolvido pela V1 não é descartado;
- [x] produto não resolvido não segue para Studio;
- [x] produto não resolvido não segue para Hub;
- [x] WAITING_VISION persiste no SQLite;
- [x] erro de resolução fica auditável;
- [x] Coordinator continua com os demais itens;
- [x] arquitetura futura da V2 está documentada;
- [x] V2 não cria pipeline paralela;
- [x] V2 possui estratégia de reconciliação de identidade;
- [x] V2 possui estratégia de ambiguidade;
- [x] V2 possui estratégia de retry limitado;
- [x] V2 possui plano de persistência de evidências;
- [x] V2 possui matriz mínima de testes definida;
- [ ] implementação da V2;
- [ ] validação da V2 contra a API real;
- [ ] ativação operacional da V2.