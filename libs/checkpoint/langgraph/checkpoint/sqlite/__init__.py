import sqlite3
import threading
from contextlib import AbstractContextManager, contextmanager
from hashlib import md5
from types import TracebackType
from typing import Any, AsyncIterator, Dict, Iterator, Optional, Sequence, Tuple

from langchain_core.runnables import RunnableConfig
from typing_extensions import Self

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    EmptyChannelError,
    SerializerProtocol,
)
from langgraph.checkpoint.serde.types import ChannelProtocol
from langgraph.checkpoint.sqlite.utils import JsonPlusSerializerCompat, search_where

_AIO_ERROR_MSG = (
    "The SqliteSaver does not support async methods. "
    "Consider using AsyncSqliteSaver instead.\n"
    "from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver\n"
    "Note: AsyncSqliteSaver requires the aiosqlite package to use.\n"
    "Install with:\n`pip install aiosqlite`\n"
    "See https://langchain-ai.github.io/langgraph/reference/checkpoints/asyncsqlitesaver"
    "for more information."
)


class SqliteSaver(BaseCheckpointSaver, AbstractContextManager):
    """A checkpoint saver that stores checkpoints in a SQLite database.

    Note:
        This class is meant for lightweight, synchronous use cases
        (demos and small projects) and does not
        scale to multiple threads.
        For a similar sqlite saver with `async` support,
        consider using [AsyncSqliteSaver][asyncsqlitesaver].

    Args:
        conn (sqlite3.Connection): The SQLite database connection.
        serde (Optional[SerializerProtocol]): The serializer to use for serializing and deserializing checkpoints. Defaults to JsonPlusSerializerCompat.

    Examples:

        >>> import sqlite3
        >>> from langgraph.checkpoint.sqlite import SqliteSaver
        >>> from langgraph.graph import StateGraph
        >>>
        >>> builder = StateGraph(int)
        >>> builder.add_node("add_one", lambda x: x + 1)
        >>> builder.set_entry_point("add_one")
        >>> builder.set_finish_point("add_one")
        >>> conn = sqlite3.connect("checkpoints.sqlite")
        >>> memory = SqliteSaver(conn)
        >>> graph = builder.compile(checkpointer=memory)
        >>> config = {"configurable": {"thread_id": "1"}}
        >>> graph.get_state(config)
        >>> result = graph.invoke(3, config)
        >>> graph.get_state(config)
        StateSnapshot(values=4, next=(), config={'configurable': {'thread_id': '1', 'thread_ts': '2024-05-04T06:32:42.235444+00:00'}}, parent_config=None)
    """  # noqa

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
        """Create a new SqliteSaver instance from a connection string.

        Args:
            conn_string (str): The SQLite connection string.

        Returns:
            SqliteSaver: A new SqliteSaver instance.

        Examples:

            In memory:

                memory = SqliteSaver.from_conn_string(":memory:")

            To disk:

                memory = SqliteSaver.from_conn_string("checkpoints.sqlite")
        """
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
        """Set up the checkpoint database.

        This method creates the necessary tables in the SQLite database if they don't
        already exist. It is called automatically when needed and should not be called
        directly by the user.
        """
        if self.is_setup:
            return

        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS checkpoints (
                thread_id TEXT NOT NULL,
                thread_ts TEXT NOT NULL,
                parent_ts TEXT,
                checkpoint BLOB,
                metadata BLOB,
                PRIMARY KEY (thread_id, thread_ts)
            );
            CREATE TABLE IF NOT EXISTS writes (
                thread_id TEXT NOT NULL,
                thread_ts TEXT NOT NULL,
                task_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                channel TEXT NOT NULL,
                value BLOB,
                PRIMARY KEY (thread_id, thread_ts, task_id, idx)
            );
            """
        )

        self.is_setup = True

    @contextmanager
    def cursor(self, transaction: bool = True) -> Iterator[sqlite3.Cursor]:
        """Get a cursor for the SQLite database.

        This method returns a cursor for the SQLite database. It is used internally
        by the SqliteSaver and should not be called directly by the user.

        Args:
            transaction (bool): Whether to commit the transaction when the cursor is closed. Defaults to True.

        Yields:
            sqlite3.Cursor: A cursor for the SQLite database.
        """
        self.setup()
        cur = self.conn.cursor()
        try:
            yield cur
        finally:
            if transaction:
                self.conn.commit()
            cur.close()

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Get a checkpoint tuple from the database.

        This method retrieves a checkpoint tuple from the SQLite database based on the
        provided config. If the config contains a "thread_ts" key, the checkpoint with
        the matching thread ID and timestamp is retrieved. Otherwise, the latest checkpoint
        for the given thread ID is retrieved.

        Args:
            config (RunnableConfig): The config to use for retrieving the checkpoint.

        Returns:
            Optional[CheckpointTuple]: The retrieved checkpoint tuple, or None if no matching checkpoint was found.

        Examples:

            Basic:
            >>> config = {"configurable": {"thread_id": "1"}}
            >>> checkpoint_tuple = memory.get_tuple(config)
            >>> print(checkpoint_tuple)
            CheckpointTuple(...)

            With timestamp:

            >>> config = {
            ...    "configurable": {
            ...        "thread_id": "1",
            ...        "thread_ts": "2024-05-04T06:32:42.235444+00:00",
            ...    }
            ... }
            >>> checkpoint_tuple = memory.get_tuple(config)
            >>> print(checkpoint_tuple)
            CheckpointTuple(...)
        """  # noqa
        with self.cursor(transaction=False) as cur:
            # find the latest checkpoint for the thread_id
            if config["configurable"].get("thread_ts"):
                cur.execute(
                    "SELECT thread_id, thread_ts, parent_ts, checkpoint, metadata FROM checkpoints WHERE thread_id = ? AND thread_ts = ?",
                    (
                        str(config["configurable"]["thread_id"]),
                        str(config["configurable"]["thread_ts"]),
                    ),
                )
            else:
                cur.execute(
                    "SELECT thread_id, thread_ts, parent_ts, checkpoint, metadata FROM checkpoints WHERE thread_id = ? ORDER BY thread_ts DESC LIMIT 1",
                    (str(config["configurable"]["thread_id"]),),
                )
            # if a checkpoint is found, return it
            if value := cur.fetchone():
                if not config["configurable"].get("thread_ts"):
                    config = {
                        "configurable": {
                            "thread_id": value[0],
                            "thread_ts": value[1],
                        }
                    }
                # find any pending writes
                cur.execute(
                    "SELECT task_id, channel, value FROM writes WHERE thread_id = ? AND thread_ts = ?",
                    (
                        str(config["configurable"]["thread_id"]),
                        str(config["configurable"]["thread_ts"]),
                    ),
                )
                # deserialize the checkpoint and metadata
                return CheckpointTuple(
                    config,
                    self.serde.loads(value[3]),
                    self.serde.loads(value[4]) if value[4] is not None else {},
                    (
                        {
                            "configurable": {
                                "thread_id": value[0],
                                "thread_ts": value[2],
                            }
                        }
                        if value[2]
                        else None
                    ),
                    [
                        (task_id, channel, self.serde.loads(value))
                        for task_id, channel, value in cur
                    ],
                )

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints from the database.

        This method retrieves a list of checkpoint tuples from the SQLite database based
        on the provided config. The checkpoints are ordered by timestamp in descending order.

        Args:
            config (RunnableConfig): The config to use for listing the checkpoints.
            filter (Optional[Dict[str, Any]]): Additional filtering criteria for metadata. Defaults to None.
            before (Optional[RunnableConfig]): If provided, only checkpoints before the specified timestamp are returned. Defaults to None.
            limit (Optional[int]): The maximum number of checkpoints to return. Defaults to None.

        Yields:
            Iterator[CheckpointTuple]: An iterator of checkpoint tuples.

        Examples:
            >>> from langgraph.checkpoint.sqlite import SqliteSaver
            >>> memory = SqliteSaver.from_conn_string(":memory:")
            ... # Run a graph, then list the checkpoints
            >>> config = {"configurable": {"thread_id": "1"}}
            >>> checkpoints = list(memory.list(config, limit=2))
            >>> print(checkpoints)
            [CheckpointTuple(...), CheckpointTuple(...)]

            >>> config = {"configurable": {"thread_id": "1"}}
            >>> before = {"configurable": {"thread_ts": "2024-05-04T06:32:42.235444+00:00"}}
            >>> checkpoints = list(memory.list(config, before=before))
            >>> print(checkpoints)
            [CheckpointTuple(...), ...]
        """
        where, param_values = search_where(config, filter, before)
        query = f"""SELECT thread_id, thread_ts, parent_ts, checkpoint, metadata
        FROM checkpoints
        {where}
        ORDER BY thread_ts DESC"""
        if limit:
            query += f" LIMIT {limit}"
        with self.cursor(transaction=False) as cur:
            cur.execute(query, param_values)
            for thread_id, thread_ts, parent_ts, value, metadata in cur:
                yield CheckpointTuple(
                    {"configurable": {"thread_id": thread_id, "thread_ts": thread_ts}},
                    self.serde.loads(value),
                    self.serde.loads(metadata) if metadata is not None else {},
                    (
                        {
                            "configurable": {
                                "thread_id": thread_id,
                                "thread_ts": parent_ts,
                            }
                        }
                        if parent_ts
                        else None
                    ),
                )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
    ) -> RunnableConfig:
        """Save a checkpoint to the database.

        This method saves a checkpoint to the SQLite database. The checkpoint is associated
        with the provided config and its parent config (if any).

        Args:
            config (RunnableConfig): The config to associate with the checkpoint.
            checkpoint (Checkpoint): The checkpoint to save.
            metadata (Optional[dict[str, Any]]): Additional metadata to save with the checkpoint. Defaults to None.

        Returns:
            RunnableConfig: The updated config containing the saved checkpoint's timestamp.

        Examples:

            >>> from langgraph.checkpoint.sqlite import SqliteSaver
            >>> memory = SqliteSaver.from_conn_string(":memory:")
            ... # Run a graph, then list the checkpoints
            >>> config = {"configurable": {"thread_id": "1"}}
            >>> checkpoint = {"ts": "2024-05-04T06:32:42.235444+00:00", "data": {"key": "value"}}
            >>> saved_config = memory.put(config, checkpoint, {"source": "input", "step": 1, "writes": {"key": "value"}})
            >>> print(saved_config)
            {"configurable": {"thread_id": "1", "thread_ts": 2024-05-04T06:32:42.235444+00:00"}}
        """
        with self.lock, self.cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO checkpoints (thread_id, thread_ts, parent_ts, checkpoint, metadata) VALUES (?, ?, ?, ?, ?)",
                (
                    str(config["configurable"]["thread_id"]),
                    checkpoint["id"],
                    config["configurable"].get("thread_ts"),
                    self.serde.dumps(checkpoint),
                    self.serde.dumps(metadata),
                ),
            )
        return {
            "configurable": {
                "thread_id": config["configurable"]["thread_id"],
                "thread_ts": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        """Store intermediate writes linked to a checkpoint.

        This method saves intermediate writes associated with a checkpoint to the SQLite database.

        Args:
            config (RunnableConfig): Configuration of the related checkpoint.
            writes (Sequence[Tuple[str, Any]]): List of writes to store, each as (channel, value) pair.
            task_id (str): Identifier for the task creating the writes.
        """
        with self.lock, self.cursor() as cur:
            cur.executemany(
                "INSERT OR REPLACE INTO writes (thread_id, thread_ts, task_id, idx, channel, value) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(config["configurable"]["thread_id"]),
                        str(config["configurable"]["thread_ts"]),
                        task_id,
                        idx,
                        channel,
                        self.serde.dumps(value),
                    )
                    for idx, (channel, value) in enumerate(writes)
                ],
            )

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Get a checkpoint tuple from the database asynchronously.

        Note:
            This async method is not supported by the SqliteSaver class.
            Use get_tuple() instead, or consider using [AsyncSqliteSaver][asyncsqlitesaver].
        """
        raise NotImplementedError(_AIO_ERROR_MSG)

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """List checkpoints from the database asynchronously.

        Note:
            This async method is not supported by the SqliteSaver class.
            Use list() instead, or consider using [AsyncSqliteSaver][asyncsqlitesaver].
        """
        raise NotImplementedError(_AIO_ERROR_MSG)
        yield

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
    ) -> RunnableConfig:
        """Save a checkpoint to the database asynchronously.

        Note:
            This async method is not supported by the SqliteSaver class.
            Use put() instead, or consider using [AsyncSqliteSaver][asyncsqlitesaver].
        """
        raise NotImplementedError(_AIO_ERROR_MSG)

    def get_next_version(self, current: Optional[str], channel: ChannelProtocol) -> str:
        """Generate the next version ID for a channel.

        This method creates a new version identifier for a channel based on its current version.

        Args:
            current (Optional[str]): The current version identifier of the channel.
            channel (BaseChannel): The channel being versioned.

        Returns:
            str: The next version identifier, which is guaranteed to be monotonically increasing.
        """
        if current is None:
            current_v = 0
        else:
            current_v = int(current.split(".")[0])
        next_v = current_v + 1
        try:
            next_h = md5(self.serde.dumps(channel.checkpoint())).hexdigest()
        except EmptyChannelError:
            next_h = ""
        return f"{next_v:032}.{next_h}"
