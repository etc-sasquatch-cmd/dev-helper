"""Read a roadmap.sh topic directory or a hand-written YAML roadmap."""

import re
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
# The upstream Backend migration topic has a doubled Markdown extension.
TOPIC_FILENAME = re.compile(r"([a-z0-9-]+)@([A-Za-z0-9_-]+)\.md(?:\.md)?")


class Topic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Identifier
    title: Title
    url: str

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or any(ord(char) <= 32 for char in value)
            or "\\" in value
        ):
            raise ValueError("use an HTTP or HTTPS topic link")
        return value


class RoadmapInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Identifier
    name: Title
    nodes: list[Topic] = Field(min_length=1)

    @field_validator("nodes")
    @classmethod
    def unique_ids(cls, nodes: list[Topic]) -> list[Topic]:
        if len({node.id for node in nodes}) != len(nodes):
            raise ValueError("topic IDs must be unique")
        return nodes


def load_roadmap(root: Path, relative_path: str) -> tuple[str, RoadmapInput]:
    """Import only from a relative path inside the local roadmap directory."""
    root = root.resolve()
    relative = Path(relative_path)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root) or path == root:
        raise ValueError("Enter a relative path inside the roadmap folder.")
    if path.is_file() and path.suffix.lower() in (".yaml", ".yml"):
        try:
            with path.open("rb") as stream:
                content_bytes = stream.read(1_000_001)
            if len(content_bytes) > 1_000_000:
                raise ValueError("YAML file is too large.")
            payload = yaml.safe_load(content_bytes.decode("utf-8-sig"))
        except (yaml.YAMLError, UnicodeDecodeError):
            raise ValueError("Could not read this YAML roadmap. Check its syntax.") from None
        roadmap = RoadmapInput.model_validate(payload)
        return f"yaml:{roadmap.id}", roadmap

    content = (path / "content").resolve()
    if not content.is_relative_to(root) or not content.is_dir():
        raise ValueError("Choose a roadmap.sh folder containing content/ or a YAML file.")
    if not re.fullmatch(r"[a-z0-9-]+", path.name):
        raise ValueError("Expected a roadmap.sh folder named with a lowercase slug.")
    topics: dict[str, Topic] = {}
    for entry in sorted(content.iterdir()):
        match = TOPIC_FILENAME.fullmatch(entry.name)
        if match and not entry.is_symlink() and entry.is_file():
            slug, node_id = match.groups()
            if node_id not in topics:
                topics[node_id] = Topic(
                    id=node_id,
                    title=slug.replace("-", " ").capitalize(),
                    url=f"https://roadmap.sh/{path.name}",
                )
    if not topics:
        raise ValueError("No roadmap.sh topic files were found in content/.")
    roadmap = RoadmapInput(
        id=path.name, name=path.name.replace("-", " ").title(), nodes=list(topics.values())
    )
    return f"roadmap.sh:{roadmap.id}", roadmap
