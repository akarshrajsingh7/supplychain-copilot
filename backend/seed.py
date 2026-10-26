import asyncio
from backend.config import MEMORY_MODE
from backend.graph.graphiti_client import TemporalMemory
from backend.tools.supply_tools import SupplyTools


async def main():
    tools = SupplyTools()
    memory = TemporalMemory(MEMORY_MODE)
    try:
        await memory.connect()
        await memory.seed(tools.graph)
        print(f"Seeded {len(tools.graph.nodes)} entities / {len(tools.graph.edges)} edges; memory={MEMORY_MODE}")
    finally:
        await memory.close()


if __name__ == "__main__":
    asyncio.run(main())
