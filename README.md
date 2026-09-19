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


## Real ecosystem bridge

The lab can execute against the real ArmoredSync/ArmoredVision/ArmoredStudio/ArmoredHub implementations without embedding an absolute machine path.

Required environment:

- `ARMORED_LEGACY_ROOT`: checkout root containing `ArmoredSync/`, `ArmoredVision/`, `ArmoredStudio/`, and `ArmoredHub/`.
- `ARMORED_SYNC_SOURCE_FACTORY`: import spec `package.module:factory` returning an object with `fetch_next()`.
- `ARMORED_ROOT`: optional lab storage root; defaults to the current directory.
- `ARMORED_STUDIO_CONFIG`: optional path to the real Studio config.

Run one recovery/reprocess cycle with:

```
python -m armored_core.run_production
```

The bridge keeps the lab database as the canonical state machine and delegates Vision, Studio and Hub work to the real modules. No absolute project path is compiled into the lab.

Telegram is not contacted by the unit-test suite. Real Telegram publication occurs only when the production bridge is explicitly configured with the real Hub credentials/session.
