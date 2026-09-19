# ArmoredCreator Test

Prototype isolated from the official repository.

## Goal

Validate the simplified sequential architecture before any migration to the official ArmoredCreator repository.

```
Telegram Source
      ↓
ArmoredSync
      ↓
Canonical SQLite DB
      ↓
ArmoredVision
      ↓
ArmoredStudio
      ↓
ArmoredHub
      ↓
Telegram Topic
      ↓
Cleanup
```

One item is processed at a time. The database is the source of truth. Each item owns one workspace:

```
storage/videos/550/
├── 550_linkoriginal.mp4
├── 550_.mp4
└── 550_<affiliate-name>.mp4
```

After confirmed publication only the immutable original remains.

## Run

```bash
python -m armored_core.demo
python -m unittest discover -s tests -v
```

No external queue, Redis, RabbitMQ, Celery, or service-specific storage is required.

## Recovery

Recovery reconciles the canonical state with the files and publisher idempotency record. It never deletes an original and never republishes an item already confirmed as published.
