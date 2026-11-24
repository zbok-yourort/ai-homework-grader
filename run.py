"""启动服务：python run.py（也可用 uvicorn app.main:app 启动）。"""
import uvicorn

from app import config

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT)
