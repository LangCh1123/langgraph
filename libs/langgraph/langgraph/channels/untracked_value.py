from contextlib import contextmanager
from typing import Generator, Generic, Optional, Sequence, Type

from langchain_core.runnables import RunnableConfig
from typing_extensions import Self

from langgraph.channels.base import BaseChannel, Value
from langgraph.errors import EmptyChannelError, InvalidUpdateError


class UntrackedValue(Generic[Value], BaseChannel[Value, Value, Value]):
    """Stores the last value received, never checkpointed."""

    def __init__(self, typ: Type[Value], guard: bool = True) -> None:
        self.typ = typ
        self.guard = guard

    def __eq__(self, value: object) -> bool:
        return isinstance(value, UntrackedValue) and value.guard == self.guard

    @property
    def ValueType(self) -> Type[Value]:
        """The type of the value stored in the channel."""
        return self.typ

    @property
    def UpdateType(self) -> Type[Value]:
        """The type of the update received by the channel."""
        return self.typ

    def checkpoint(self) -> Value:
        raise EmptyChannelError()

    @contextmanager
    def from_checkpoint(
        self, checkpoint: Optional[Value], config: RunnableConfig
    ) -> Generator[Self, None, None]:
        empty = self.__class__(self.typ, self.guard)
        try:
            yield empty
        finally:
            try:
                del empty.value
            except AttributeError:
                pass

    def update(self, values: Sequence[Value]) -> bool:
        if len(values) == 0:
            return False
        if len(values) != 1 and self.guard:
            raise InvalidUpdateError(
                "UntrackedValue can only receive one value per step."
            )

        self.value = values[-1]
        return True

    def get(self) -> Value:
        try:
            return self.value
        except AttributeError:
            raise EmptyChannelError()
