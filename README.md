# ArmoredCreator Test

Laboratório isolado da reconstrução do ArmoredCreator.

> **Regra:** este repositório é o laboratório. O repositório oficial e a branch
> `audit/baseline-2026-09-19` são preservados e não fazem parte deste ciclo.

## Objetivo

Reconstruir a pipeline funcional já validada no backup, mas com uma arquitetura
canônica, portátil, idempotente e recuperável:

```
Telegram fonte
    ↓
ArmoredSync
    ↓
SQLite (estado canônico)
    ↓
ArmoredVision
    ↓
ArmoredStudio
    ├── ANALYSIS
    │   ├── metadata
    │   ├── black bars
    │   ├── banner
    │   ├── VEO
    │   └── Gemini
    ↓
    ├── PLANNING
    ├── PROCESSING
    │   ├── áudio
    │   ├── RVC
    │   ├── banner/intro
    │   ├── música
    │   └── FFmpeg
    ↓
    └── VALIDATION
    ↓
ArmoredHub
    ↓
Telegram destino
    ↓
confirmação
    ↓
PUBLISHED
    ↓
cleanup
```

Há **um único ArmoredStudio**. V1 e V2 não são modos alternativos:
os detectores V1 alimentam o planejamento e o processamento V2 executa o
resultado.

## Storage canônico

Cada item possui exatamente um workspace:

```
storage/
├── database/
│   └── armoredcreator.db
├── videos/
│   └── {item_id}/
│       ├── {item_id}_finallinkoriginal.mp4   # IMUTÁVEL
│       ├── {item_id}_.mp4                    # working
│       └── {item_id}_{final_do_link}.mp4     # resultado
├── logs/
└── backups/
```

Não são permitidas cópias de conteúdo em `storage/sync`, `storage/queue`,
`storage/pipeline`, `storage/input` ou `storage/output`.

Artefatos temporários de processamento, quando necessários, devem ficar dentro
do workspace do item e ser removidos ao terminar.

## Contrato de nomes

Para:

```
https://.../abc/finaldomeulinknovo
```

e item `550`:

```
storage/videos/550/550_finallinkoriginal.mp4
storage/videos/550/550_.mp4
storage/videos/550/550_finaldomeulinknovo.mp4
```

O sufixo final vem do último segmento da URL afiliada. O DB continua sendo a
fonte de verdade; nomes de arquivo são uma projeção determinística.

## Studio unificado

O código importado do backup foi separado por responsabilidade:

### Análise preservada

- `modules/v1/video.py` — metadata do vídeo.
- `modules/v1/blackbar.py` — barras pretas, espacial + temporal.
- `modules/v1/banner_analyzer.py` — análise visual inicial.
- `modules/v1/banner.py` — início de voz/corte temporal.
- `modules/v1/veo_detector.py` — VEO FAST/FULL.
- `modules/v1/gemini_detector.py` — Gemini FAST/FULL.
- `modules/v1/export_planner.py` — consolidação geométrica/temporal.
- `modules/v1/export_plan_validator.py` — validação do plano.
- `modules/v1/export_executor.py` — executor V1 de referência.

### Processamento preservado

- `modules/v2/rvc_voice.py` — RVC.
- `modules/v2/editing/tool_paths.py` — resolução portátil de executáveis.
- `modules/v2/editing/finalizer.py` — finalização FFmpeg em uma passada.

### Orquestração nova

`ArmoredStudio/unified.py` é a fronteira única:

1. recebe o item;
2. preserva o original;
3. normaliza o working;
4. executa toda a análise;
5. cria e valida o plano;
6. executa o processamento V2;
7. valida o resultado;
8. devolve somente `working_path` e `result_path` ao Core.

O serviço não conhece `storage/input`, `storage/output` ou checkout antigo.

## RVC e assets

Os recursos V2 devem pertencer ao próprio projeto ou ser explicitamente
configurados por ambiente:

```
ARMORED_FFMPEG
ARMORED_FFPROBE
ARMORED_STUDIO_MUSIC
ARMORED_STUDIO_BANNER
ARMORED_STUDIO_RVC_VOICE
ARMORED_STUDIO_INTRO
ARMORED_STUDIO_INTRO_POSITION
```

Nenhum caminho absoluto de outra máquina ou outro checkout deve ser usado.

A ausência de asset/RVC em processamento real é erro explícito; o sistema não
deve degradar silenciosamente para uma cópia.

O modo de cópia continua existindo **somente para testes determinísticos**:

```
ARMORED_STUDIO_ALLOW_COPY=1
ARMORED_STUDIO_FORCE_COPY=1
```

Nesse modo, a análise real é deliberadamente pulada porque os testes de
contrato podem usar bytes que não são um MP4. Isso não representa produção.

## Recovery e publicação

O SQLite permanece canônico.

Estados:

```
RECEIVED → VISION → STUDIO → PUBLISHING → PUBLISHED → CLEANUP → DONE
```

Em reinício:

- original nunca é apagado;
- working/result existentes são reconciliados;
- publicação confirmada não é repetida;
- Telegram `UNKNOWN` não autoriza republicação;
- cleanup só ocorre depois de `PUBLISHED`.

## Auditoria do backup

O backup confirmou duas famílias de comportamento que precisam ser preservadas:

1. **V1:** detectar → planejar → validar → exportar.
2. **V2:** extrair áudio → RVC → finalizer, com transformação audiovisual e
   áudio no FFmpeg final.

O antigo `batch_processor.py`, `core.output.output_manager`, `storage/input`,
`storage/output` e `storage/temp` não são importados como arquitetura.
Eles são apenas referência comportamental.

### Próxima otimização deliberada

A primeira integração prioriza **correção e preservação de comportamento**.
Quando os testes reais estiverem verdes, o próximo passo é fundir o
`crop_final`/cortes temporais do plano V1 diretamente no filter graph do
`finalizer.py`, evitando uma segunda passada FFmpeg quando possível.

## Testes

Local:

```powershell
cd C:\Users\Administrador\Downloads\ArmoredCreator
git pull
python -m pytest -q
```

O último estado confirmado antes desta etapa tinha:

```
19 passed
```

Cada alteração do laboratório deve manter essa suíte verde antes de avançar
para o E2E Telegram real.

## Ordem de validação

1. Storage/naming.
2. Sync real → workspace canônico.
3. Vision real → affiliate metadata.
4. Studio analysis completo.
5. Studio V2 real (FFmpeg + RVC + assets).
6. Hub dry-run.
7. Recovery pós-Studio.
8. Recovery pós-envio Telegram.
9. Telegram real ponta a ponta.
10. Somente então considerar a reconstrução fechada.

## Regra de segurança

Nunca alterar a branch:

```
audit/baseline-2026-09-19
```

Ela existe como fotografia imutável do estado anterior à reconstrução.
