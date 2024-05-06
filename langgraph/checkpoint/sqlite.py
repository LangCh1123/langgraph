import pickle
import sqlite3
import threading
from contextlib import AbstractContextManager, contextmanager
from types import TracebackType
from typing import Any, Iterator, Optional

from langchain_core.runnables import RunnableConfig
from typing_extensions import Self

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    SerializerProtocol,
)
from langgraph.serde.jsonplus import JsonPlusSerializer


# for backwards compat we continue to support loading pickled checkpoints
class JsonPlusSerializerCompat(JsonPlusSerializer):
    def loads(self, data: bytes) -> Any:
        if data.startswith(b"\x80") and data.endswith(b"."):
            return pickle.loads(data)
        return super().loads(data)


class SqliteSaver(BaseCheckpointSaver, AbstractContextManager):
    serde = JsonPlusSerializerCompat()

    conn: sqlite3.Connection

    is_setup: bool

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        serde: Optional[SerializerProtocol] = None,
    ) -> None:
        super().__init__(serde=serde)
        self.conn = conn
        self.is_setup = False
        self.lock = threading.Lock()

    @classmethod
    def from_conn_string(cls, conn_string: str) -> "SqliteSaver":
        return SqliteSaver(
            conn=sqlite3.connect(
                conn_string,
                # https://ricardoanderegg.com/posts/python-sqlite-thread-safety/
                check_same_thread=False,
            )
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        __exc_type: Optional[type[BaseException]],
        __exc_value: Optional[BaseException],
        __traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        return self.conn.close()

    def setup(self) -> None:
        if self.is_setup:
            return

        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                thread_id TEXT NOT NULL,
                thread_ts TEXT NOT NULL,
                parent_ts TEXT,
                checkpoint BLOB,
                metadata BLOB,
                PRIMARY KEY (thread_id, thread_ts)
            );
            """
        )

        self.is_setup = True

    @contextmanager
    def cursor(self, transaction: bool = True):
        self.setup()
        cur = self.conn.cursor()
        try:
            yield cur
        finally:
            if transaction:
                self.conn.commit()
            cur.close()

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        with self.cursor(transaction=False) as cur:
            if config["configurable"].get("thread_ts"):
                cur.execute(
                    "SELECT checkpoint, parent_ts, metadata FROM checkpoints WHERE thread_id = ? AND thread_ts = ?",
                    (
                        str(config["configurable"]["thread_id"]),
                        str(config["configurable"]["thread_ts"]),
                    ),
                )
                if value := cur.fetchone():
                    return CheckpointTuple(
                        config,
                        self.serde.loads(value[0]),
                        self.serde.loads(value[2]) if value[2] is not None else {},
                        {
                            "configurable": {
                                "thread_id": config["configurable"]["thread_id"],
                                "thread_ts": value[1],
                            }
                        }
                        if value[1]
                        else None,
                    )
            else:
                cur.execute(
                    "SELECT thread_id, thread_ts, parent_ts, checkpoint, metadata FROM checkpoints WHERE thread_id = ? ORDER BY thread_ts DESC LIMIT 1",
                    (str(config["configurable"]["thread_id"]),),
                )
                if value := cur.fetchone():
                    return CheckpointTuple(
                        {
                            "configurable": {
                                "thread_id": value[0],
                                "thread_ts": value[1],
                            }
                        },
                        self.serde.loads(value[3]),
                        self.serde.loads(value[4]) if value[4] is not None else {},
                        {
                            "configurable": {
                                "thread_id": value[0],
                                "thread_ts": value[2],
                            }
                        }
                        if value[2]
                        else None,
                    )

    def list(
        self,
        config: RunnableConfig,
        *,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        query = (
            "SELECT thread_id, thread_ts, parent_ts, checkpoint, metadata FROM checkpoints WHERE thread_id = ? ORDER BY thread_ts DESC"
            if before is None
            else "SELECT thread_id, thread_ts, parent_ts, checkpoint, metadata FROM checkpoints WHERE thread_id = ? AND thread_ts < ? ORDER BY thread_ts DESC"
        )
        if limit:
            query += f" LIMIT {limit}"
        with self.cursor(transaction=False) as cur:
            cur.execute(
                query,
                (str(config["configurable"]["thread_id"]),)
                if before is None
                else (
                    str(config["configurable"]["thread_id"]),
                    before["configurable"]["thread_ts"],
                ),
            )
            for thread_id, thread_ts, parent_ts, value, metadata in cur:
                yield CheckpointTuple(
                    {"configurable": {"thread_id": thread_id, "thread_ts": thread_ts}},
                    self.serde.loads(value),
                    self.serde.loads(metadata) if metadata is not None else {},
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "thread_ts": parent_ts,
                        }
                    }
                    if parent_ts
                    else None,
                )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
    ) -> RunnableConfig:
        with self.lock, self.cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO checkpoints (thread_id, thread_ts, parent_ts, checkpoint, metadata) VALUES (?, ?, ?, ?, ?)",
                (
                    str(config["configurable"]["thread_id"]),
                    checkpoint["ts"],
                    config["configurable"].get("thread_ts"),
                    self.serde.dumps(checkpoint),
                    self.serde.dumps(metadata),
                ),
            )
        return {
            "configurable": {
                "thread_id": config["configurable"]["thread_id"],
                "thread_ts": checkpoint["ts"],
            }
        }
