# ArmoredCreator Test

Laboratório isolado da nova arquitetura sequencial do ArmoredCreator.


## Objetivo da reconstrução

Este repositório **não é um desenvolvimento da pipeline funcional do zero**.

A pipeline original do ArmoredCreator foi previamente executada e validada de ponta a ponta no projeto de referência. O backup preserva esse comportamento funcional validado:

**Telegram fonte → ArmoredSync → ArmoredVision → ArmoredStudio → ArmoredHub → Telegram**

A finalidade deste laboratório é **reconstruir e unificar essa implementação**, preservando o comportamento que já funciona e fortalecendo a arquitetura.

Objetivos principais:

- unificar as implementações e responsabilidades;
- remover módulos duplicados, órfãos e código legado;
- eliminar dependências de caminhos ou checkouts externos;
- manter o comportamento funcional já validado como referência;
- estabelecer o SQLite como estado canônico da pipeline;
- impedir processamento e publicação duplicados;
- tornar a publicação idempotente;
- garantir que o original permaneça imutável;
- implementar recuperação determinística após falhas e reinícios;
- garantir cleanup seguro somente após confirmação da publicação.

O **ArmoredCreator_BACKUP é a referência comportamental**, não uma dependência de execução. A reconstrução deve ser capaz de reproduzir o fluxo validado do backup sem importar código, estado ou runtime do projeto antigo.

Portanto, a validação desta reconstrução deve responder principalmente a:

> **O novo núcleo preserva o comportamento já validado enquanto elimina duplicações e adiciona proteção, idempotência e recovery determinístico?**

Não se trata de reinventar ou redesenhar a pipeline funcional que já foi comprovada.

## Arquitetura canônica

```
ArmoredSync
    ↓
SQLite (fonte de verdade)
    ↓
ArmoredVision
    ↓
ArmoredStudio
    ↓
ArmoredHub
    ↓
confirmação
    ↓
PUBLISHED
    ↓
cleanup
```

Um item por vez. Cada item possui um workspace próprio e o original é imutável.
Não existe `ARMORED_LEGACY_ROOT`, import dinâmico para outro checkout ou estado
paralelo JSON controlando a pipeline.

## Módulos

- `ArmoredSync/service.py`: aquisição + ingestão canônica.
- `ArmoredVision/service.py`: resolução Shopee + Affiliate Open API.
- `ArmoredStudio/service.py`: processamento por Job via FFmpeg.
- `ArmoredHub/service.py`: publicação idempotente; dry-run por padrão no laboratório.
- `armored_core/`: SQLite, state machine, recovery e storage compartilhados.

## Execução offline

Para validar a cadeia sem Telegram/Shopee:

```
python -m armored_core.demo
python -m unittest discover -s tests -v
```

Os testes não acessam Telegram.

## Execução local da composição

O `Coordinator.build()` monta os quatro módulos locais. O Sync usa
`storage/input` como fonte determinística. Para um item local, defina:

```
ARMORED_TEST_ORIGINAL_URL=https://shopee.com.br/...
ARMORED_STUDIO_ALLOW_COPY=1
ARMORED_HUB_DRY_RUN=1
```

Para produção de vídeo real, instale FFmpeg e remova o fallback de cópia.

A Vision real exige:

```
SHOPEE_APP_ID=...
SHOPEE_SECRET_KEY=...
```

O Hub Telegram real permanece opt-in e será conectado somente na etapa de E2E
Telegram, depois que Sync → Vision → Studio → Hub estiver fechado e validado.

## Recovery

A recuperação considera todos os estados incompletos persistidos
(`RECEIVED`, `VISION`, `STUDIO`, `PUBLISHING`, `RECOVERY`, `FAILED`).
Nunca remove o original e não publica novamente uma publicação já confirmada.

## Limpeza arquitetural

Foram removidos do laboratório:

- bridge para checkout legado;
- carregador dinâmico de adapters;
- `run_production` dependente do repositório antigo;
- pacote de adapters vazio;
- workflow GitHub Actions duplicado.

O repositório de backup continua sendo apenas referência; a branch de preservação
não é alterada por esta limpeza.
