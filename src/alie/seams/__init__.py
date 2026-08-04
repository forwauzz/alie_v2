"""The five seams for later cloud migration (PRD §13.4). Five, and no more.

No generic AI-provider framework, no plugin API, no ORM-agnostic layer — two real
implementations before generalising anything.

| Seam           | Interface                                        | Module                  |
|----------------|--------------------------------------------------|-------------------------|
| Model          | `complete(prompt, task) -> text`                 | `seams.model`           |
| Parser         | `parse(page) -> blocks`                          | `seams.parser`          |
| Blob store     | `get/put(key)`                                   | `stores.blobs`          |
| Metadata store | plain SQL, no SQLite-isms                        | `stores.db`             |
| Job runner     | `enqueue(stage, unit_ids)`                       | `stores.runs` + `jobs`  |
"""

from . import model, parser

__all__ = ["model", "parser"]
