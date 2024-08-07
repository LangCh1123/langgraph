from contextlib import AbstractAsyncContextManager, AbstractContextManager
from types import TracebackType
from typing import Optional


class NoneContextManager(AbstractContextManager, AbstractAsyncContextManager):
    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        __exc_type: Optional[type[BaseException]],
        __exc_value: Optional[BaseException],
        __traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        return

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        __exc_type: Optional[type[BaseException]],
        __exc_value: Optional[BaseException],
        __traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        return
