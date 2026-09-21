from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Running this file directly from scripts/ puts scripts/ on sys.path.
# Project packages live one level above, at the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from armored_core.database import Database
from armored_core.models import Item, State
from armored_core.storage import Storage
from ArmoredHub.service import ArmoredHub
from ArmoredStudio.service import ArmoredStudio


def load_project_env(root: Path) -> None:
    for path in (
        root / "storage" / "credentials" / "telegram" / "user.env",
        root / "storage" / "credentials" / "telegram" / "bot.env",
        root / "storage" / "credentials" / "shopee" / "affiliate.env",
        root / ".env",
    ):
        if path.is_file():
            load_dotenv(path, override=False)


def make_retest_item(source: Item, content_id: str, workspace: Path) -> Item:
    workspace.mkdir(parents=True, exist_ok=True)
    return Item(
        content_id=content_id,
        telegram_message_id=content_id,
        state=State.STUDIO,
        workspace=workspace,
        original_path=source.original_path,
        working_path=None,
        result_path=None,
        affiliate_name=source.affiliate_name,
        affiliate_url=source.affiliate_url,
        source_id=source.source_id,
        original_url=source.original_url,
        topic_id=source.topic_id,
        topic_name=source.topic_name,
        original_sha256=source.original_sha256,
    )


async def inspect_telegram(root: Path, chat_id: str, message_id: str) -> None:
    try:
        from telethon import TelegramClient
        from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo
    except ImportError as exc:
        raise RuntimeError("Telethon ausente para inspeção pós-publicação") from exc

    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID/TELEGRAM_API_HASH ausentes")

    session = root / "storage" / "credentials" / "telegram" / "session" / "armoredsync"
    client = TelegramClient(str(session), int(api_id), api_hash, request_retries=0, connection_retries=0)
    try:
        await client.start()
        message = await client.get_messages(int(chat_id), ids=int(message_id))
        if message is None:
            raise RuntimeError(f"Mensagem Telegram {message_id} não encontrada")

        document = getattr(message, "document", None)
        print(f"TELEGRAM MESSAGE ID: {message.id}")
        print(f"VIDEO: {bool(getattr(message, 'video', None))}")
        print(f"DOCUMENT: {bool(document)}")

        if document is None:
            return

        print(f"MIME: {getattr(document, 'mime_type', None)}")
        for attribute in getattr(document, "attributes", []) or []:
            if isinstance(attribute, DocumentAttributeVideo):
                print(
                    "VIDEO ATTRIBUTES: "
                    f"width={getattr(attribute, 'w', None)} "
                    f"height={getattr(attribute, 'h', None)} "
                    f"duration={getattr(attribute, 'duration', None)} "
                    f"round_message={getattr(attribute, 'round_message', None)} "
                    f"supports_streaming={getattr(attribute, 'supports_streaming', None)}"
                )
            elif isinstance(attribute, DocumentAttributeFilename):
                print(f"FILENAME: {attribute.file_name}")
    finally:
        if client.is_connected():
            await client.disconnect()


def run_one(root: Path, source: Item) -> None:
    if not source.original_path.is_file():
        raise FileNotFoundError(f"ORIGINAL ausente para {source.content_id}: {source.original_path}")
    if not source.affiliate_name or not source.affiliate_url:
        raise RuntimeError(
            f"Item {source.content_id} não possui affiliate_name/affiliate_url; "
            "não é possível reproduzir o envio com a mesma identidade."
        )

    if os.getenv("ARMORED_STUDIO_FORCE_COPY") == "1":
        raise RuntimeError(
            "ARMORED_STUDIO_FORCE_COPY=1 está ativo. Desative-o para que este reteste "
            "passe pelo Studio/RVC real."
        )

    test_id = f"retest-{source.content_id}"
    storage = Storage(root)
    workspace = storage.workspace(test_id)

    source_publication = None
    try:
        source_publication = Database(root / "storage" / "database" / "armoredcreator.db").publication(source.content_id)
    except Exception:
        source_publication = None

    previous_group_id = os.environ.get("ARMORED_CREATOR_GROUP_ID")
    if source_publication and source_publication["destination_chat_id"]:
        os.environ["ARMORED_CREATOR_GROUP_ID"] = str(source_publication["destination_chat_id"])

    try:
        with tempfile.TemporaryDirectory(prefix="armoredcreator-retest-") as temp_dir:
            test_db = Database(Path(temp_dir) / "retest.db")
            try
        test_db.create_item(
            telegram_message_id=test_id,
            original_path=source.original_path,
            source_id=source.source_id,
            topic_id=source.topic_id,
            topic_name=source.topic_name,
            original_url=source.original_url,
        )
        test_db.set_vision(test_id, source.affiliate_name, source.affiliate_url)
        test_db.transition(test_id, State.STUDIO, "controlled-studio-retest")
        item = test_db.get(test_id)

                print(f"\n=== RETESTE REAL {source.content_id} -> {test_id} ===")
                print(f"ORIGINAL: {source.original_path}")
                print("STUDIO: executando análise + RVC + finalização real...")
                studio = ArmoredStudio(root)
                result = studio.process(item)

                if not result.result_path.is_file() or result.result_path.stat().st_size <= 0:
                    raise RuntimeError("Studio não produziu resultado válido")

                test_db.set_result(test_id, result.result_path)
                test_db.transition(test_id, State.PUBLISHING, "controlled-studio-complete")
                item = test_db.get(test_id)

                print(f"STUDIO RESULT: {result.result_path}")
                print("HUB: enviando pelo mesmo send_video real usado em produção...")
                hub = ArmoredHub(root, test_db)
                publication = hub._publish_telegram(item, result.result_path)

                if not publication.confirmed:
                    raise RuntimeError("Publicação do reteste não foi confirmada")

                row = test_db.publication(test_id)
                message_id = str(row["published_message_id"])
                chat_id = str(row["destination_chat_id"])

                print(f"PUBLICATION: CONFIRMED message_id={message_id}")
                print(f"DESTINATION CHAT: {chat_id}")
                print(f"DESTINATION TOPIC: {row['destination_topic_id']}")
                print("TELEGRAM: lendo metadata via MTProto...")
                asyncio.run(inspect_telegram(root, chat_id, message_id))
            finally:
                test_db.close()
    finally:
        if previous_group_id is None:
            os.environ.pop("ARMORED_CREATOR_GROUP_ID", None)
        else:
            os.environ["ARMORED_CREATOR_GROUP_ID"] = previous_group_id
        if os.getenv("ARMORED_RETEST_KEEP_OUTPUT") != "1":
            shutil.rmtree(workspace, ignore_errors=True)
            print(f"LIMPEZA: workspace temporário {workspace} removido")
        else:
            print(f"LIMPEZA: preservada por ARMORED_RETEST_KEEP_OUTPUT=1")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reteste controlado dos vídeos problemáticos passando pelo Studio/RVC real "
            "e pelo Hub/Telegram real, sem alterar o estado dos itens originais."
        )
    )
    parser.add_argument("items", nargs="+", help="IDs dos itens existentes, por exemplo: 550 552")
    args = parser.parse_args()

    root = Path(os.getenv("ARMORED_ROOT") or Path(__file__).resolve().parents[1]).resolve()
    os.environ["ARMORED_ROOT"] = str(root)
    load_project_env(root)

    source_db = Database(root / "storage" / "database" / "armoredcreator.db")
    try:
        for item_id in args.items:
            source = source_db.get(str(item_id))
            run_one(root, source)
    finally:
        source_db.close()

    print("\nRETESTE CONCLUÍDO: Studio real + publicação real + inspeção MTProto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
