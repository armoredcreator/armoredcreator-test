# ArmoredStudio

> **STATUS: CONGELADO**
>
> O ArmoredStudio é o componente de processamento de vídeo da arquitetura principal do ArmoredCreator. A partir deste estado, ele é tratado como **runtime estável/congelado**: não deve receber refatorações, reorganizações de arquitetura, cópias de módulos ou alterações motivadas pelo ArmoredStudioEdit.

## 1. Objetivo

O ArmoredStudio executa o processamento de vídeo da pipeline principal do ArmoredCreator.

Na arquitetura principal, o fluxo é:

```
Telegram / Sync
      ↓
ArmoredVision
      ↓
ArmoredStudio
      ↓
ArmoredHub
      ↓
Publicação Telegram
```

O ArmoredStudio não é o orquestrador geral da aplicação. O controle da pipeline, recuperação, estado e publicação pertence às camadas superiores do ArmoredCreator.

## 2. Regra de congelamento

O congelamento significa:

- não alterar a arquitetura interna apenas para atender o ArmoredStudioEdit;
- não criar uma segunda implementação de black-bar;
- não criar uma segunda implementação de VEO detector;
- não criar uma segunda implementação de Gemini detector;
- não criar uma segunda implementação de RVC;
- não mover os módulos funcionais existentes;
- não duplicar modelos RVC;
- não duplicar o ambiente Python do RVC;
- não alterar o `.env` principal para configurar o ArmoredStudioEdit;
- não introduzir dependência do ArmoredStudioEdit no fluxo principal;
- preservar os caminhos canônicos e o comportamento já validado.

Qualquer evolução futura deve ser feita de forma explícita e independente do estado congelado.

## 3. Organização funcional

Os componentes funcionais preservados do ArmoredStudio estão concentrados nas áreas:

```
ArmoredStudio/
├── analysis/
│   ├── blackbar.py
│   ├── veo_detector.py
│   └── gemini_detector.py
│
├── processing/
│   └── rvc.py
│
├── unified.py
│
├── runtime/
│   └── rvc/
│       ├── env/
│       ├── models/
│       └── output/
│
└── ...
```

Os módulos de análise são reutilizados pelo ArmoredStudioEdit através de chamadas diretas, sem copiar sua implementação.

O ArmoredStudioEdit pode utilizar:

- `ArmoredStudio.analysis.blackbar`
- `ArmoredStudio.analysis.veo_detector`
- `ArmoredStudio.analysis.gemini_detector`
- `ArmoredStudio.processing.rvc`

O ArmoredStudioEdit **não deve** importar ou assumir controle de:

- `ArmoredStudio.unified`
- `ArmoredStudio.service`
- planners;
- executors;
- finalizers;
- mecanismos de orquestração da pipeline principal.

## 4. Armazenamento e caminhos

O ArmoredStudio pertence à arquitetura principal e utiliza os diretórios canônicos definidos pelo projeto.

Os dados persistentes da pipeline principal ficam separados do runtime do RVC.

Estrutura conceitual:

```
storage/
├── database/
├── videos/
├── logs/
└── backups/

ArmoredStudio/runtime/rvc/
├── env/
├── models/
└── output/
```

O runtime RVC é um recurso compartilhado de execução/modelos. Ele não deve ser copiado para o ArmoredStudioEdit.

## 5. RVC

O processamento RVC existente é centralizado em:

```
ArmoredStudio/processing/rvc.py
```

O runtime utilizado atualmente é:

```
ArmoredStudio/runtime/rvc/
```

Com:

```
ArmoredStudio/runtime/rvc/env/
ArmoredStudio/runtime/rvc/models/
ArmoredStudio/runtime/rvc/output/
```

O código resolve o ambiente Python RVC em:

```
ArmoredStudio/runtime/rvc/env/Scripts/python.exe
```

A raiz pode ser explicitamente apontada por:

```
ARMORED_RVC_ROOT
```

Quando essa variável não é fornecida, o módulo usa o runtime RVC associado ao próprio ArmoredStudio.

### Vozes disponíveis

As vozes atualmente presentes no runtime são:

```
becca
elsa
jessie
leticia
marilia
melody
sarah
```

O comando amigável `/rebecca` do ArmoredStudioEdit corresponde internamente à pasta/modelo `becca`.

### Modelos

Cada voz possui pelo menos um arquivo `.pth`, que é o requisito principal para localização do modelo. Arquivos `.index` são opcionais e utilizados quando disponíveis.

Não renomear, mover ou duplicar os modelos para resolver necessidades do ArmoredStudioEdit.

## 6. Uso pelo ArmoredStudioEdit

O ArmoredStudioEdit é um processo separado.

Ele pode executar diariamente em paralelo ao ArmoredCreator e utilizar os componentes funcionais congelados do ArmoredStudio sem alterar o ArmoredStudio.

Fluxo do StudioEdit:

```
Telegram topic
    ↓
download temporário
    ↓
blackbar
    ↓
VEO detector
    ↓
Gemini detector
    ↓
RVC opcional
    ↓
envio ao mesmo tópico
    ↓
limpeza do workspace temporário
```

A configuração persistente do StudioEdit fica em:

```
ArmoredStudioEdit/config/config.json
```

Os segredos do StudioEdit ficam exclusivamente em:

```
ArmoredStudioEdit/.env
```

O `.env` não deve ser versionado.

## 7. Comandos do ArmoredStudioEdit

### Iniciar

No PowerShell:

```powershell
cd C:\Users\Administrador\Downloads\ArmoredCreator
python -m ArmoredStudioEdit.app --telegram
```

### Status do RVC

No tópico configurado do StudioEdit:

```
/rvc
```

Exemplo de resposta:

```
RVC=ON | voz=melody
```

### Ativar RVC

```
/rvc on
```

### Desativar RVC

```
/rvc off
```

### Alterar voz explicitamente

```
/rvc voice melody
```

As vozes aceitas atualmente são:

```
/rvc voice becca
/rvc voice elsa
/rvc voice jessie
/rvc voice leticia
/rvc voice marilia
/rvc voice melody
/rvc voice sarah
```

### Atalhos rápidos de voz

Também existem comandos diretos:

```
/rebecca
/elsa
/jessie
/leticia
/marilia
/melody
/sarah
```

Cada comando:

1. seleciona a voz;
2. ativa o RVC;
3. salva a configuração;
4. aplica a configuração ao próximo vídeo.

O comando `/rebecca` é um alias especial para a pasta/modelo `becca`.

## 8. Isolamento entre os processos

É esperado que os dois sistemas possam permanecer ligados ao mesmo tempo:

```
START_ALL.bat
    ↓
ArmoredCreator
```

e, separadamente:

```
START_STUDIO_EDIT.bat
    ↓
ArmoredStudioEdit
```

Eles possuem responsabilidades diferentes.

### ArmoredCreator

Responsável pela produção principal:

```
Telegram
→ Sync
→ Vision
→ Studio
→ Hub
→ publicação
```

### ArmoredStudioEdit

Responsável pela edição automática independente:

```
Telegram topic
→ análise
→ processamento
→ RVC opcional
→ retorno ao tópico
```

Um processo não deve iniciar, parar ou reconfigurar o outro.

## 9. Concorrência do RVC

O runtime RVC é compartilhado como recurso de execução e modelos.

Os trabalhos continuam isolados porque cada execução do StudioEdit utiliza seu próprio workspace temporário.

Não criar cópias como:

```
ArmoredStudioEdit/runtime/rvc/
ArmoredStudioEdit/models/
ArmoredStudioEdit/env/
```

A fonte oficial dos modelos RVC continua sendo:

```
ArmoredStudio/runtime/rvc/
```

Se ArmoredCreator e ArmoredStudioEdit executarem RVC simultaneamente, ambos podem consumir CPU e memória da mesma máquina. Isso é uma questão de capacidade de recursos, não de mistura de arquivos, desde que cada job mantenha seu workspace próprio.

## 10. Comandos de manutenção do repositório

### Ver branch atual

```powershell
git branch --show-current
```

### Atualizar a branch de trabalho

Use a branch correspondente ao desenvolvimento atual do repositório.

Exemplo:

```powershell
git pull origin feature/armoredstudio-edit
```

### Ver alterações locais

```powershell
git status
```

### Ver últimos commits

```powershell
git log --oneline --decorate -10
```

### Executar testes

Na raiz do projeto:

```powershell
python -m pytest -q
```

Para testes específicos:

```powershell
python -m pytest tests -q
```

## 11. Verificação do runtime RVC

Para verificar se o ambiente RVC existe:

```powershell
Test-Path .\ArmoredStudio\runtime\rvc\env\Scripts\python.exe
```

Para listar as vozes:

```powershell
Get-ChildItem .\ArmoredStudio\runtime\rvc\models -Directory
```

Para verificar os modelos:

```powershell
Get-ChildItem .\ArmoredStudio\runtime\rvc\models -Recurse -File |
    Where-Object { $_.Extension -in '.pth','.index' } |
    Select-Object FullName,Length
```

Para verificar o módulo RVC:

```powershell
python -c "from ArmoredStudio.processing import rvc; print(rvc.RVC_ROOT); print(rvc.DEFAULT_VOICE)"
```

## 12. Validação operacional já realizada

O runtime RVC foi validado com conversão real.

A execução confirmou:

- ambiente Python RVC localizado;
- modelo `melody.pth` carregado;
- conversão de voz executada;
- arquivo `audio_rvc.wav` produzido;
- vídeo enviado de volta ao Telegram;
- mensagem publicada com sucesso;
- fallback para CPU funcionando quando GPU NVIDIA não está disponível.

O encerramento por `Ctrl+C` após uma execução concluída gera a sequência normal de cancelamento do processo assíncrono. Isso não caracteriza falha do processamento que já terminou.

## 13. Regras para alterações futuras

Antes de alterar o ArmoredStudio, verificar se a necessidade realmente pertence ao componente congelado.

### Não fazer

- copiar módulos para o StudioEdit;
- criar segundo detector Gemini;
- criar segundo detector VEO;
- criar segundo detector de black-bar;
- criar segundo runtime RVC;
- duplicar modelos;
- alterar o `.env` principal para o StudioEdit;
- misturar workspaces do StudioEdit com `storage/` da pipeline principal;
- introduzir dependência circular entre Studio e StudioEdit.

### Fazer

- manter o ArmoredStudio estável;
- reutilizar seus módulos funcionais por import direto quando permitido;
- manter configuração do StudioEdit em `ArmoredStudioEdit/config/config.json`;
- manter credenciais do StudioEdit em `ArmoredStudioEdit/.env`;
- manter o runtime RVC único;
- validar alterações do StudioEdit sem modificar o contrato do Studio.

## 14. Diagnóstico rápido

### RVC não encontra uma voz

Verifique:

```powershell
Get-ChildItem .\ArmoredStudio\runtime\rvc\models -Directory
```

A pasta da voz precisa existir e conter pelo menos um `.pth`.

### RVC não encontra o Python

Verifique:

```powershell
Test-Path .\ArmoredStudio\runtime\rvc\env\Scripts\python.exe
```

### StudioEdit não inicia

Verifique primeiro:

```powershell
git status
python --version
python -m ArmoredStudioEdit.app --telegram
```

Não altere o ArmoredStudio para corrigir um problema exclusivo do StudioEdit.

### Problema de credenciais

Não coloque credenciais no README, no código ou em commits.

O StudioEdit usa seu próprio:

```
ArmoredStudioEdit/.env
```

## 15. Estado de referência

Este README documenta o contrato operacional do ArmoredStudio no estado congelado.

O princípio central é:

> **ArmoredStudio é infraestrutura funcional preservada. ArmoredStudioEdit é um consumidor independente desses componentes, não uma nova versão do ArmoredStudio.**

Qualquer mudança que altere esse contrato deve ser tratada como uma nova decisão arquitetural, e não como manutenção rotineira.
