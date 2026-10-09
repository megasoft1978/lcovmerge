"""Create the small synthetic files used by the hosted Action smoke workflow."""

from pathlib import Path


workspace = Path.cwd()
(workspace / ".luna-tmp").mkdir(parents=True, exist_ok=True)
shards = workspace / "coverage" / "shards"
shards.mkdir(parents=True, exist_ok=True)
(shards / "unit.info").write_text(
    "TN:unit\nSF:src/demo.c\nDA:1,1\nBRDA:1,0,0,1\nend_of_record\n",
    encoding="utf-8",
)
(shards / "integration.info").write_text(
    "TN:integration\nSF:src/demo.c\nDA:1,2\nBRDA:1,0,0,0\nend_of_record\n",
    encoding="utf-8",
)
(workspace / "coverage" / "bad.info").write_text(
    "SF:src/broken.c\nDA:1,broken\n",
    encoding="utf-8",
)
