from huey import SqliteHuey

from cvm.config import settings

settings.data_dir.mkdir(parents=True, exist_ok=True)
huey = SqliteHuey("cvm", filename=str(settings.queue_path))
