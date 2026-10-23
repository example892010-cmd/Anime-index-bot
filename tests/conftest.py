import pytest_asyncio

from app.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = await Database(str(tmp_path / "test.db")).connect()
    yield database
    await database.close()
