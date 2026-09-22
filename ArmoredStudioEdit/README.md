# ArmoredStudioEdit

Aplicação isolada responsável pelo tópico Telegram ArmoredStudio.

## Regra arquitetural

O diretório ArmoredStudio existente é somente uma dependência. Seus arquivos não são duplicados nem modificados.

O Edit somente seleciona e chama as implementações existentes de:
- barras pretas: ArmoredStudio.analysis.blackbar
- VEO: ArmoredStudio.analysis.veo_detector
- Gemini: ArmoredStudio.analysis.gemini_detector
- RVC: ArmoredStudio.processing.rvc

Todas as demais configurações, fila, estado, Telegram, armazenamento e orquestração pertencem ao ArmoredStudioEdit.
