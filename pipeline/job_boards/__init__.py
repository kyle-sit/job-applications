"""
Job board adapters.

Each module in this package is an adapter for one job-board platform (or a
family of boards that share infrastructure). Adapters expose a uniform
contract:

    fetch_board(board_config: dict) -> list[JobRecord]

where `board_config` comes from a profile's `job_boards.json` and includes
at least an `id`, a `base_url`, and a `listing_strategy` (or whatever
strategy field the adapter expects). `JobRecord` is the adapter's own
dataclass; adapters are responsible for normalizing whatever the board
exposes into a consistent set of fields that the downstream pipeline can
ingest.

Adapters in this package:

  - getro_html : Getro-substrate boards (Climate Draft, Elemental Impact,
                 Terra.do) — uses __NEXT_DATA__ or HTML listing extraction
                 plus unified JSON-LD JobPosting detail enrichment.
"""
