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
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    import uvicorn
    from .web.api import create_app
    from .web.mast import mast_extension
    from .observability.mast import MastPlugin
    from .observability.mast.judge import OpenAIMastJudge
    from .observability.mast.service import MastJobs, MastService

    try:
        from dotenv import load_dotenv
    except ImportError:
        pass  # Environment variables work without the optional dotenv package.
    else:
        load_dotenv(args.env_file, override=False)

    framework = Framework(SQLiteHistory(args.data / "history.sqlite"),
                          versions=GitVersions(args.data / "history.git"), plugins=(ActivityPlugin(),))
    artifacts = FileArtifacts(args.data / "artifacts")
    mast = MastService(framework, MastPlugin(OpenAIMastJudge()),
                       MastJobs(args.data / "mast.sqlite"), artifacts)
    uvicorn.run(create_app(framework, artifacts, extensions=(mast_extension(mast),)),
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()
