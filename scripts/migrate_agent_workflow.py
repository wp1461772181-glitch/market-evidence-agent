"""Explicit additive setup for LangGraph's built-in PostgreSQL checkpoint tables."""
import asyncio

from app.agent_workflow.checkpoints import open_postgres_checkpointer
from app.agent_workflow.settings import checkpoint_database_url


async def main() -> None:
    async with open_postgres_checkpointer(checkpoint_database_url()) as saver:
        await saver.setup()


if __name__ == "__main__":
    asyncio.run(main())
