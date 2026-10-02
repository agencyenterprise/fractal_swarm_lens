import argparse
from pathlib import Path

from .application.framework import Framework
from .adapters.artifacts import FileArtifacts
from .adapters.git import GitVersions
from .adapters.sqlite import SQLiteHistory
from .plugins import ActivityPlugin


def main():
    parser = argparse.ArgumentParser(description="Explore a framework application's recorded runs")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    import uvicorn
    from .web.api import create_app

    framework = Framework(SQLiteHistory(args.data / "history.sqlite"),
                          versions=GitVersions(args.data / "history.git"), plugins=(ActivityPlugin(),))
    uvicorn.run(create_app(framework, FileArtifacts(args.data / "artifacts")), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
