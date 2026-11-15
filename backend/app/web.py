import os

from fastapi.staticfiles import StaticFiles

from app.main import app


app.mount("/", StaticFiles(directory=os.environ["STATIC_DIRECTORY"], html=True), name="frontend")
