from __future__ import annotations

import os
from pathlib import Path

from .coordinator import Coordinator
from .runtime import ProductionBindings
from .adapters.legacy import LegacyHubAdapter, LegacySourceAdapter, LegacyStudioAdapter, LegacyVisionAdapter


def build_legacy_coordinator(root: Path | None = None) -> Coordinator:
    """Assemble o laboratório contra as implementações reais do ecossistema.

    Não contém caminhos absolutos. ARMORED_LEGACY_ROOT aponta apenas para a
    raiz do checkout que contém ArmoredSync/ArmoredVision/ArmoredStudio/ArmoredHub.
    """

    legacy_root = os.getenv("ARMORED_LEGACY_ROOT")
    if not legacy_root:
        raise RuntimeError("ARMORED_LEGACY_ROOT não configurado")

    import sys
    root_path = str(Path(legacy_root).resolve())
    if root_path not in sys.path:
        sys.path.insert(0, root_path)

    vision_mod = __import__("ArmoredVision.modules.v1.vision", fromlist=["ArmoredVision"])
    studio_config = __import__("ArmoredStudio.core.config_loader", fromlist=["ConfigLoader"])
    studio_controller = __import__("ArmoredStudio.core.controller", fromlist=["ArmoredController"])
    studio_paths = __import__("ArmoredStudio.core.paths", fromlist=["Paths"])
    studio_logger = __import__("ArmoredStudio.core.logger", fromlist=["setup_logger"])
    hub_mod = __import__("ArmoredHub.publisher", fromlist=["publish_item"])
    source_factory_spec = os.getenv("ARMORED_SYNC_SOURCE_FACTORY")
    if not source_factory_spec:
        raise RuntimeError("ARMORED_SYNC_SOURCE_FACTORY não configurado")

    from .runtime import load_symbol
    source = load_symbol(source_factory_spec)()

    config_path = Path(os.getenv("ARMORED_STUDIO_CONFIG", str(Path(legacy_root) / "ArmoredStudio" / "config" / "config.json")))
    config = studio_config.ConfigLoader(config_path)
    paths = studio_paths.Paths(Path(legacy_root) / "ArmoredStudio", config)
    paths.create()
    logger = studio_logger.setup_logger(Path(root or Path.cwd()) / "storage" / "logs")
    controller = studio_controller.ArmoredController(config=config, paths=paths, logger=logger)

    vision = vision_mod.ArmoredVision()
    publisher = hub_mod
    bindings = ProductionBindings(
        vision=LegacyVisionAdapter(vision),
        studio=LegacyStudioAdapter(controller, Path(root or Path.cwd()) / "storage"),
        publisher=_ModulePublisherAdapter(publisher),
        source=LegacySourceAdapter(source),
    )
    return Coordinator(
        __import__("armored_core.database", fromlist=["Database"]).Database(
            __import__("armored_core.storage", fromlist=["Storage"]).Storage(root).database / "armoredcreator.db"
        ),
        __import__("armored_core.storage", fromlist=["Storage"]).Storage(root),
        bindings.vision,
        bindings.studio,
        bindings.publisher,
        bindings.source,
    )


class _ModulePublisherAdapter:
    def __init__(self, module):
        self.module = module

    def check_publication(self, item):
        from .models import PublicationCheck
        state = self.module.get_publication_state(str(item.id))
        if state and state.get("status") == "published":
            return PublicationCheck.CONFIRMED
        if state and state.get("status") in {"publishing", "publication_uncertain"}:
            return PublicationCheck.UNKNOWN
        return PublicationCheck.ABSENT

    def publish(self, item):
        import asyncio
        result = asyncio.run(self.module.publish_item({
            "identity": str(item.id),
            "source": {"source_id": item.source_id, "message_id": item.telegram_message_id},
            "original_shopee_link": item.original_url,
            "affiliate_link": item.affiliate_url,
            "output_file": item.result_path,
        }))
        if result.get("status") not in {"published", "already_published"}:
            raise RuntimeError(result.get("detail") or result.get("status"))
        return type("PublicationResult", (), {
            "confirmed": True,
            "message_id": result.get("telegram_message_id"),
        })()
